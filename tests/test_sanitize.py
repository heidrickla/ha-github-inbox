"""The sanitiser is the boundary between GitHub's HTML and the panel."""

from __future__ import annotations

import pytest

from custom_components.github_inbox.sanitize import (
    preview,
    safe_url,
    sanitize,
    text_html,
)

LINK = 'target="_blank" rel="noopener noreferrer nofollow"'


@pytest.mark.parametrize(
    "dirty",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg onload=alert(1)><circle/></svg>",
        "<iframe src=https://example.com></iframe>",
        "<style>body{display:none}</style>",
        "<object data=x></object>",
        "<math><mi>x</mi></math>",
        "<template><img src=x onerror=alert(1)></template>",
        "<form action=https://example.com><input name=a></form>",
    ],
)
def test_active_content_leaves_nothing_executable(dirty: str) -> None:
    out = sanitize(dirty)
    for word in (
        "<script",
        "onerror",
        "onload",
        "<iframe",
        "<svg",
        "<style",
        "<object",
        "<form",
        "alert(1)<",
    ):
        assert word not in out.lower()


def test_script_text_is_dropped_with_the_tag() -> None:
    assert sanitize("<p>a</p><script>steal()</script><p>b</p>") == "<p>a</p><p>b</p>"


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "JaVaScRiPt:alert(1)",
        "jav&#x09;ascript:alert(1)",
        "&#106;avascript:alert(1)",
        " javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "//evil.example/x",
        "/relative/path",
    ],
)
def test_unsafe_links_lose_their_href_and_keep_their_text(href: str) -> None:
    out = sanitize(f'<a href="{href}">click</a>')
    assert out == "click"


def test_safe_links_open_in_a_new_tab_with_no_other_attributes() -> None:
    out = sanitize(
        '<a href="https://github.com/x" onclick="x()" class="c" id="i">x</a>'
    )
    assert out == f'<a href="https://github.com/x" {LINK}>x</a>'


def test_attributes_are_dropped_except_the_allowed_ones() -> None:
    out = sanitize(
        '<p style="color:red" class="x">a</p><ol start="3" type="a"><li>b</li></ol>'
        '<table><tr><td align="center" width="9">c</td><th align="evil">d</th></tr></table>'
    )
    assert out == (
        '<p>a</p><ol start="3"><li>b</li></ol>'
        '<table><tr><td align="center">c</td><th>d</th></tr></table>'
    )


def test_images_and_videos_become_links() -> None:
    out = sanitize(
        '<img src="https://user-images.example/a.png" alt="shot"><video src="https://v.example/v.mp4"></video>'
    )
    assert out == (
        f'<a href="https://user-images.example/a.png" {LINK}>image: shot</a>'
        f'<a href="https://v.example/v.mp4" {LINK}>video</a>'
    )


def test_an_image_inside_a_link_is_text_and_an_unsafe_image_is_its_alt() -> None:
    assert sanitize(
        '<a href="https://a.example"><img src="https://b.example/i.png" alt="x"></a>'
    ) == (f'<a href="https://a.example" {LINK}>[image: x]</a>')
    assert sanitize('<img src="javascript:x" alt="y">') == "[image: y]"


def test_links_do_not_nest() -> None:
    out = sanitize(
        '<a href="https://a.example">a<a href="https://b.example">b</a>c</a>'
    )
    assert out.count("<a ") == 1


def test_task_list_checkboxes_are_disabled() -> None:
    out = sanitize(
        '<li><input type="checkbox" checked> done</li><li><input type="text" value="x"> no</li>'
    )
    assert out == '<li><input type="checkbox" disabled checked> done</li><li> no</li>'


def test_text_is_escaped_again() -> None:
    assert (
        sanitize("<p>&lt;script&gt;x&lt;/script&gt; &amp;</p>")
        == "<p>&lt;script&gt;x&lt;/script&gt; &amp;</p>"
    )


def test_unknown_tags_are_unwrapped_and_output_balanced() -> None:
    assert (
        sanitize("<g-emoji alias=x>🎉</g-emoji><p><strong>a<em>b</p>c")
        == "🎉<p><strong>a<em>b</em></strong></p>c"
    )
    assert sanitize("</div>x</p>") == "x"


def test_self_closed_allowed_tags_close_at_once() -> None:
    assert sanitize("<br/><hr/><p/>x") == "<br><hr><p></p>x"


def test_empty_input() -> None:
    assert sanitize(None) == ""
    assert sanitize("") == ""
    assert text_html(None) == ""
    assert preview(None) == ""


def test_safe_url() -> None:
    assert safe_url("https://x.example") == "https://x.example"
    assert safe_url("mailto:a@b.example") == "mailto:a@b.example"
    assert safe_url("java\nscript:alert(1)") is None
    assert safe_url(None) is None


def test_text_html_escapes_markdown_source() -> None:
    assert (
        text_html("a <b>\nline\n\n  second  ")
        == "<p>a &lt;b&gt;<br>line</p><p>second</p>"
    )


def test_preview_is_one_line_cut_at_a_word() -> None:
    html = "<p>Hello   <b>world</b></p><script>no</script><pre>code\nhere</pre>"
    assert preview(html) == "Hello world code here"
    long = "<p>" + "word " * 60 + "</p>"
    out = preview(long, limit=30)
    assert out.endswith("…")
    assert len(out) <= 31
    assert preview("<p>" + "x" * 50 + "</p>", limit=10) == "x" * 10 + "…"
