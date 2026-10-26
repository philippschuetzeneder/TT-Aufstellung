from app.xttv_schedule_import import parse_meids_from_html


def test_parse_meids_from_html_deduplicates_and_preserves_order():
    html = """
    <a href="index.php?meid=462234">A</a>
    <a href="index.php?meid=462235">B</a>
    <a href="index.php?meid=462234">dup</a>
    """
    assert parse_meids_from_html(html) == [462234, 462235]
