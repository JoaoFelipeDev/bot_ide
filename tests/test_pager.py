"""Testes da logica de paginacao (id_monitor.gui.pager) - sem abrir janela."""
from id_monitor.gui.pager import PAGE_SIZES, page_count, slice_page


def test_page_sizes_offered():
    assert PAGE_SIZES == (5, 10, 20, 50, 100)


def test_page_count():
    assert page_count(0, 10) == 1
    assert page_count(10, 10) == 1
    assert page_count(11, 10) == 2
    assert page_count(198, 50) == 4


def test_slice_page_basic_and_clamping():
    items = list(range(23))
    first, page = slice_page(items, 0, 10)
    assert first == list(range(10)) and page == 0
    last, page = slice_page(items, 99, 10)  # passou do fim: vai para a ultima
    assert last == [20, 21, 22] and page == 2
    before, page = slice_page(items, -5, 10)
    assert page == 0 and before[0] == 0


def test_slice_page_empty_list():
    assert slice_page([], 3, 20) == ([], 0)
