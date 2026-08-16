from datetime import date

from app.rc_import import parse_player_history


def test_parse_rc_current_uncertainty_and_three_year_history():
    html = """
    <html><body>
      <h1>Schützeneder, Philipp</h1>
      <div>1402±62</div>
      <table>
        <tr><th>Date</th><th>Event</th><th>Initial Rating</th><th>Point Change</th><th>Final Rating</th></tr>
        <tr><td>2026-03-29</td><td>OÖTTV</td><td>1389±39</td><td>+13</td><td>1402±38</td></tr>
        <tr><td>2023-10-08</td><td>OÖTTV</td><td>1189±71</td><td>+64</td><td>1253±75</td></tr>
        <tr><td>2023-07-01</td><td>old</td><td>1200±50</td><td>+1</td><td>1201±49</td></tr>
      </table>
    </body></html>
    """
    parsed = parse_player_history(html, today=date(2026, 8, 16))
    assert parsed["name"] == "Schützeneder, Philipp"
    assert parsed["current"] == {
        "observed_at": "2026-08-16",
        "rc_rating": 1402.0,
        "rc_deviation": 62.0,
    }
    assert [row["observed_at"] for row in parsed["history"]] == ["2026-03-29", "2023-10-08"]
    assert parsed["history"][0]["rc_rating"] == 1402.0
    assert parsed["history"][0]["rc_deviation"] == 38.0
