"""Testes do redutor de HTML usado antes de mandar paginas para a IA."""
from __future__ import annotations

from id_monitor.ai.html_trim import trim_html


def test_removes_scripts_styles_and_hidden_elements():
    html = (
        "<html><head><title>x</title><style>.a{color:red}</style></head><body>"
        "<script>var segredo = 1;</script>"
        '<div hidden>escondido</div><div style="display: none">escondido2</div>'
        "<p>visivel</p><svg><path d='M0'/></svg></body></html>"
    )
    out = trim_html(html)
    assert "visivel" in out
    for removed in ("segredo", "color:red", "escondido", "<svg", "<style"):
        assert removed not in out


def test_keeps_only_useful_attributes():
    html = (
        '<a id="x" class="btn primary" href="/produto/1" onclick="evil()" style="color:red" '
        'data-testid="card" data-tracking-payload="blablablablablablablablablablablablablabla">ver</a>'
    )
    out = trim_html(html)
    assert 'id="x"' in out and 'class="btn primary"' in out and 'href="/produto/1"' in out
    assert 'data-testid="card"' in out
    assert "onclick" not in out and "style=" not in out and "tracking" not in out


def test_collapses_repeated_siblings():
    items = "".join(f'<li class="card"><b>Livro {i}</b></li>' for i in range(20))
    out = trim_html(f"<ul>{items}</ul>")
    assert out.count('<li class="card">') == 3
    assert "+17 elementos iguais omitidos" in out
    assert "Livro 0" in out and "Livro 19" not in out


def test_drop_layout_removes_header_nav_footer_unless_disabled():
    html = "<header><input name='q'></header><nav>menu</nav><main>conteudo</main><footer>rodape</footer>"
    out = trim_html(html, drop_layout=True)
    assert "conteudo" in out and "menu" not in out and "rodape" not in out and "name=\"q\"" not in out
    home = trim_html(html, drop_layout=False)
    assert 'name="q"' in home


def test_focus_prices_keeps_only_results_area():
    menu = "".join(f"<li><a href='/cat{i}'>Categoria {i}</a></li>" for i in range(30))
    products = "".join(
        f'<div class="spot"><h3>Livro {i}</h3><span class="preco">R$ {10 + i},90</span></div>' for i in range(8)
    )
    html = (
        f"<body><div class='topo'><ul>{menu}</ul></div>"
        f"<div class='conteudo'><div class='lista'>{products}</div></div>"
        "<div class='rodape'>Contato</div></body>"
    )
    out = trim_html(html, focus_prices=True)
    assert "Categoria" not in out and "Contato" not in out
    assert 'class="spot"' in out and "R$ 10,90" in out
    assert "area dos resultados dentro de" in out


def test_respects_max_chars():
    items = "".join(f'<div class="c{i}"><p>{"texto " * 40}</p></div>' for i in range(300))
    out = trim_html(f"<body>{items}</body>", max_chars=2000)
    assert len(out) <= 2000 + len("<!-- cortado no limite de tamanho -->")


def test_survives_malformed_html():
    out = trim_html("<div><p>aberto sem fechar<span>x</div></b></i><li>solto")
    assert "aberto sem fechar" in out and "solto" in out
