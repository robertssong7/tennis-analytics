"""Live tournament state builder.

Pulls Tennis Abstract's per-tournament forecast page and the charting
project's meta index, reconciles them into a canonical state object,
and writes data/live/tournament_state.json.

Source: https://www.tennisabstract.com/ (Jeff Sackmann, CC BY-NC-SA 4.0).
Non-commercial use; attribution preserved in the schema's data_source field
and on the project's About page.
"""
