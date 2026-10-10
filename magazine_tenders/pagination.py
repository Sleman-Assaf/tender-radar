# magazine_tenders/pagination.py
"""
A paginator whose FIRST page is a little larger than the rest.

Why this exists
---------------
The News page lays its cards out in a grid that is four columns wide on a
desktop, and a page whose card count is not a multiple of four ends with an
orphan card and empty cells beside it.

Page one is the odd one out: its featured strip consumes the lead story plus
the Latest rail before the grid starts, so the grid gets five fewer cards than
every other page. With 24 a page, that is 19 - one short of five clean rows.

The alternatives were worse. Shortening the Latest rail to three would have
made the grid divide evenly but left the rail visibly shorter than the hero
beside it. Letting the last row stretch would have made those cards wider than
every other row's. Giving page one a single extra card changes no design and
no ordering - it moves one card up a page.

What it cannot do
-----------------
The grid reflows to three and two columns as the window narrows, and no single
page size divides evenly by all three. This targets the four-column desktop
case, which is where the hole was reported; 24 already divides by two and
three for every page after the first.
"""
from django.core.paginator import Page, Paginator
from django.utils.functional import cached_property


class _UnevenPage(Page):
    """
    A page that knows where it really starts.

    Django computes start_index/end_index from a uniform per_page, so
    with a wider first page every later page reported itself one row
    early - "showing 25-48" for rows 26 to 49. Nothing renders those
    today; a Showing X-Y of N line added later would have been wrong
    with nothing to catch it.
    """

    def start_index(self) -> int:
        if self.paginator.count == 0:
            return 0
        return self.paginator._page_bounds(self.number)[0] + 1

    def end_index(self) -> int:
        if self.paginator.count == 0:
            return 0
        return min(self.paginator._page_bounds(self.number)[0]
                   + len(self.object_list), self.paginator.count)


class FirstPageLargerPaginator(Paginator):
    """
    `Paginator`, except the first page holds `per_page + first_page_extra`.

    Every other page holds `per_page`. Page numbering, counts and the
    `page_range` a template iterates over all stay correct, because the only
    thing overridden is where each page starts and stops.
    """

    def __init__(self, object_list, per_page, first_page_extra=0, **kwargs):
        self.first_page_extra = max(0, int(first_page_extra or 0))
        super().__init__(object_list, per_page, **kwargs)

    @property
    def first_page_size(self) -> int:
        return self.per_page + self.first_page_extra

    @cached_property
    def num_pages(self) -> int:
        """
        Pages, counting `orphans` the way `page()` does.

        Both have to agree. When only `page()` folded the tail, a list of 26
        with per_page 24, one extra and three orphans reported two pages while
        page one already held all 26 - so page two served row 26 a second time
        and the reader saw 27 cards for 26 announcements. Latent while
        ListView leaves paginate_orphans at 0; a silent duplication the moment
        anyone sets it.
        """
        if self.count == 0 and not self.allow_empty_first_page:
            return 0
        if self.count <= self.first_page_size + self.orphans:
            return 1
        remaining = self.count - self.first_page_size
        pages = 1 + -(-remaining // self.per_page)
        # The tail that page() folds into the page before it.
        tail = remaining - (pages - 2) * self.per_page
        if pages > 1 and 0 < tail <= self.orphans:
            pages -= 1
        return pages

    def _page_bounds(self, number: int) -> tuple:
        """Where page `number` starts and stops, before orphan folding."""
        if number == 1:
            return 0, self.first_page_size
        bottom = self.first_page_size + (number - 2) * self.per_page
        return bottom, bottom + self.per_page

    def page(self, number):
        """One page, with the first page's wider slice."""
        number = self.validate_number(number)
        bottom, top = self._page_bounds(number)
        # Django's own orphan handling, unchanged: a final page with only a
        # few rows is folded into the one before it.
        if top + self.orphans >= self.count:
            top = self.count
        return self._get_page(self.object_list[bottom:top], number, self)

    def _get_page(self, *args, **kwargs):
        """Hand out the page class that knows about the wider first page."""
        return _UnevenPage(*args, **kwargs)
