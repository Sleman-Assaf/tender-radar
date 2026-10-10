"""
Strip credentials out of the shipped MySQL dump.

`payload/mysql/initial_tenderradar.sql` is the dataset every fresh installation
starts from. It is a real mysqldump of a working system, so it carries whatever
that system had configured — including the Microsoft Graph client secret, the
LDAP bind password, the WhatsApp API key, the OpenAI key and live session
cookies. Shipping it unmodified hands those to every recipient of the installer.

build_installer.assert_no_secrets() never caught this: it scans payload/app, and
the dump lives in payload/mysql.

What this module does
---------------------
  * reads the dump's own CREATE TABLE blocks for column order, type and
    nullability — no hard-coded column positions to drift out of date
  * blanks every column whose name matches a credential pattern, using a value
    the column can actually hold (NULL if nullable, '' for text, 0 for numeric)
  * drops the rows of tables that hold live sessions outright
  * re-reads the result and fails if any flagged column still has a value

What it deliberately does NOT do
--------------------------------
`auth_user.password` is kept. Those are salted PBKDF2 hashes, not plaintext, and
blanking them locks every user out of a fresh installation with no way back in —
the dump is also the only source of the accounts. See KEEP below.

Usage:
    python desktop/sanitize_dump.py [path/to/dump.sql]        # in place
    python desktop/sanitize_dump.py src.sql -o out.sql
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

# Column names that hold a credential. Patterns, not a fixed list: a column added
# later called `smtp_password` or `xyz_api_key` is caught without an edit here.
#
# Written to NOT match the neighbouring innocents in this schema:
#   graph_token_expires_at / graph_token_acquired_at  (datetimes)
#   prompt_tokens_est                                 (a counter)
#   session_key / routing_key                         (identifiers, not secrets)
SECRET_COLUMN = re.compile(
    r'''(
          password | passwd
        | _secret$ | ^secret | client_secret
        | api_key
        | access_token | refresh_token | _token$
        | credential
    )''',
    re.I | re.X,
)

# (table, column) pairs the pattern matches but that must survive. Removing these
# would break the installation rather than protect anything.
KEEP = {
    ('auth_user', 'password'),          # PBKDF2 hashes; the only way to log in
}

# Tables whose rows are dropped entirely. A session row is a live credential:
# restoring one lets anyone with the dump resume that user's browser session.
TRUNCATE = {
    'django_session',
}

CREATE_RE = re.compile(r'^CREATE TABLE `([^`]+)` \(')
# The remainder is matched with .* rather than [^,]* on purpose: a type such as
# decimal(10,2) contains a comma, and stopping at it would fail the match, drop
# the column from the ordered list and silently shift every index after it.
COLUMN_RE = re.compile(r'^\s+`([^`]+)`\s+([a-z]+)(.*)$', re.I)
INSERT_RE = re.compile(r'^INSERT INTO `([^`]+)` VALUES ')

TEXTUAL = {'char', 'varchar', 'text', 'tinytext', 'mediumtext', 'longtext',
           'blob', 'tinyblob', 'mediumblob', 'longblob', 'binary', 'varbinary',
           'json', 'enum', 'set'}
NUMERIC = {'int', 'tinyint', 'smallint', 'mediumint', 'bigint', 'decimal',
           'numeric', 'float', 'double', 'bit', 'year'}


class Column:
    __slots__ = ('name', 'type', 'nullable')

    def __init__(self, name: str, type_: str, nullable: bool):
        self.name, self.type, self.nullable = name, type_.lower(), nullable

    def blank(self) -> str:
        """A literal this column will accept that carries no information."""
        if self.nullable:
            return 'NULL'
        if self.type in TEXTUAL:
            return "''"
        if self.type in NUMERIC:
            return '0'
        return "''"


def read_schema(path: Path) -> dict[str, list[Column]]:
    """table -> ordered columns, parsed from the dump's own DDL."""
    tables: dict[str, list[Column]] = {}
    current: str | None = None
    with path.open(encoding='utf-8', errors='surrogateescape', newline='') as fh:
        for line in fh:
            if current is None:
                m = CREATE_RE.match(line)
                if m:
                    current = m.group(1)
                    tables[current] = []
                continue
            if line.startswith(')'):
                current = None
                continue
            m = COLUMN_RE.match(line)
            if m:
                tables[current].append(
                    Column(m.group(1), m.group(2), 'NOT NULL' not in m.group(3).upper()))
    return tables


def split_tuples(clause: str) -> list[list[str]]:
    """
    ``(1,'a',NULL),(2,'b\\'c',NULL)``  ->  ``[['1', "'a'", 'NULL'], ...]``

    Values are returned as raw SQL literals so they can be written straight back
    out: no unquoting, no re-escaping, nothing to get wrong on the round trip.
    Handles both mysqldump escape styles — backslash and doubled quote.
    """
    rows: list[list[str]] = []
    cur: list[str] = []
    buf: list[str] = []
    depth = 0
    in_str = False
    i, n = 0, len(clause)
    while i < n:
        ch = clause[i]
        if in_str:
            if ch == '\\':
                buf.append(ch)
                i += 1
                if i < n:
                    buf.append(clause[i])
                    i += 1
                continue
            if ch == "'":
                if i + 1 < n and clause[i + 1] == "'":      # '' inside a string
                    buf.append("''")
                    i += 2
                    continue
                in_str = False
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_str = True
            buf.append(ch)
            i += 1
            continue
        if ch == '(':
            depth += 1
            if depth == 1:
                cur, buf = [], []
                i += 1
                continue
        elif ch == ')':
            depth -= 1
            if depth == 0:
                cur.append(''.join(buf))
                rows.append(cur)
                cur, buf = [], []
                i += 1
                continue
        elif ch == ',' and depth == 1:
            cur.append(''.join(buf))
            buf = []
            i += 1
            continue
        if depth == 0:
            i += 1                                          # between tuples
            continue
        buf.append(ch)
        i += 1
    return rows


def secret_columns(table: str, cols: list[Column]) -> list[int]:
    return [i for i, c in enumerate(cols)
            if SECRET_COLUMN.search(c.name) and (table, c.name) not in KEEP]


def sanitize(src: Path, dst: Path | None = None, log=print) -> dict:
    """Rewrite `src` (in place unless `dst` is given). Returns a report."""
    schema = read_schema(src)
    targets = {t: secret_columns(t, cols) for t, cols in schema.items()}
    targets = {t: idx for t, idx in targets.items() if idx}

    for table, idx in sorted(targets.items()):
        names = ', '.join(schema[table][i].name for i in idx)
        log(f'   {table}: {names}')
    for table in sorted(TRUNCATE):
        if table in schema:
            log(f'   {table}: all rows dropped')

    in_place = dst is None
    out_path = src.with_suffix(src.suffix + '.tmp') if in_place else dst
    report = {'tables': {}, 'dropped': {}, 'values_blanked': 0, 'rows_dropped': 0}

    with src.open(encoding='utf-8', errors='surrogateescape', newline='') as fin, \
         out_path.open('w', encoding='utf-8', errors='surrogateescape', newline='') as fout:
        for line in fin:
            m = INSERT_RE.match(line)
            if not m:
                fout.write(line)
                continue
            table = m.group(1)

            if table in TRUNCATE:
                n = len(split_tuples(line[m.end():]))
                report['dropped'][table] = report['dropped'].get(table, 0) + n
                report['rows_dropped'] += n
                continue

            idx = targets.get(table)
            if not idx:
                fout.write(line)
                continue

            cols = schema[table]
            rows = split_tuples(line[m.end():])
            # Blanking by position is only safe if the parsed DDL and the parsed
            # row agree on how many columns there are. If they do not, something
            # about the parse is wrong and writing a blank into row[i] would
            # destroy a different column — refuse instead of guessing.
            for row in rows:
                if len(row) != len(cols):
                    raise SystemExit(
                        f'sanitize_dump: {table} has {len(cols)} columns in its '
                        f'DDL but {len(row)} values in a row — refusing to edit')
            changed = 0
            for row in rows:
                for i in idx:
                    if row[i] in ('NULL', "''"):
                        continue                    # already empty
                    row[i] = cols[i].blank()
                    changed += 1
            report['tables'][table] = report['tables'].get(table, 0) + changed
            report['values_blanked'] += changed
            body = ','.join('(' + ','.join(r) + ')' for r in rows)
            fout.write(f'INSERT INTO `{table}` VALUES {body};\n')

    if in_place:
        out_path.replace(src)
        final = src
    else:
        final = out_path

    verify(final, log=log)
    return report


def verify(path: Path, log=print) -> None:
    """Re-read the finished dump and fail if any flagged column still has data."""
    schema = read_schema(path)
    targets = {t: secret_columns(t, c) for t, c in schema.items()}
    targets = {t: i for t, i in targets.items() if i}
    leaks: list[str] = []
    with path.open(encoding='utf-8', errors='surrogateescape', newline='') as fh:
        for line in fh:
            m = INSERT_RE.match(line)
            if not m:
                continue
            table = m.group(1)
            if table in TRUNCATE:
                leaks.append(f'{table}: rows still present')
                continue
            idx = targets.get(table)
            if not idx:
                continue
            for row in split_tuples(line[m.end():]):
                for i in idx:
                    if i < len(row) and row[i] not in ('NULL', "''"):
                        leaks.append(f'{table}.{schema[table][i].name}')
    if leaks:
        for leak in sorted(set(leaks)):
            log(f'   LEAK: {leak}')
        raise SystemExit(f'sanitize_dump: {len(set(leaks))} credential(s) survived')
    log('   verified: no credential columns carry a value')


def is_clean(path: Path) -> bool:
    """True if `path` would survive verify() unchanged."""
    try:
        verify(path, log=lambda *_: None)
        return True
    except SystemExit:
        return False


def ensure_clean(path: Path, backup_dir: Path | None = None, log=print) -> bool:
    """
    Sanitize `path` unless it is already clean. Called by build_installer so a
    dump can never be packaged with credentials in it, whoever produced it.
    Returns True if the file was modified.
    """
    if is_clean(path):
        log('   dump already sanitized')
        return False
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / (path.name + '.orig')
        if not backup.exists():
            shutil.copy2(path, backup)
            log(f'   original kept at {backup}')
    report = sanitize(path, log=log)
    log(f'   {report["values_blanked"]} value(s) blanked, '
        f'{report["rows_dropped"]} row(s) dropped')
    return True


def main(argv: list[str]) -> int:
    dst = None
    rest = list(argv)
    if '-o' in rest:
        k = rest.index('-o')
        dst = Path(rest[k + 1])
        del rest[k:k + 2]
    args = [a for a in rest if not a.startswith('-')]
    src = Path(args[0]) if args else (
        Path(__file__).resolve().parent / 'payload' / 'mysql' / 'initial_tenderradar.sql')
    if not src.is_file():
        raise SystemExit(f'not found: {src}')
    print(f'sanitizing {src}  ({src.stat().st_size / 1048576:.0f} MB)')
    report = sanitize(src, dst)
    print(f'  {report["values_blanked"]} value(s) blanked, '
          f'{report["rows_dropped"]} session row(s) dropped')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
