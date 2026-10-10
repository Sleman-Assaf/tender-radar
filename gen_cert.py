# Path: TenderRadar/gen_cert.py
# Run: python gen_cert.py
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import datetime, pathlib

ssl_dir = pathlib.Path('ssl')
ssl_dir.mkdir(exist_ok=True)

key = rsa.generate_private_key(65537, 2048, default_backend())
subject = issuer = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, 'JO'),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'AJi Group'),
    x509.NameAttribute(NameOID.COMMON_NAME, 'radar.AJGroup.com.local'),
])
cert = (x509.CertificateBuilder()
    .subject_name(subject)
    .issuer_name(issuer)
    .public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(datetime.datetime.utcnow())
    .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
    .add_extension(x509.SubjectAlternativeName([
        x509.DNSName('radar.AJGroup.com.local'),
        x509.DNSName('radar.ajigroup.com'),
        x509.DNSName('localhost'),
        x509.IPAddress(__import__("ipaddress").IPv4Address("172.10.0.19")),
    ]), critical=False)
    .sign(key, hashes.SHA256(), default_backend()))

(ssl_dir / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
(ssl_dir / 'key.pem').write_bytes(key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))

print('[OK] ssl/cert.pem created')
print('[OK] ssl/key.pem  created')
print('[OK] Valid 10 years')
print('[OK] CN=radar.AJGroup.com.local')
print('[OK] SAN: radar.AJGroup.com.local, radar.ajigroup.com, localhost, 172.10.0.19')
