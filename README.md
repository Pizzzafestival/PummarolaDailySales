# Pummarola daily sales

`lavu_report.py` is the read-only live Lavu daily reporter. It pages through the
legacy `getData` orders-table endpoint directly and emits aggregate figures only; it
never prints credentials, raw responses, or individual orders.

The Falls and Coral Springs remain configured but are temporarily disabled. Run the
report for yesterday (America/New_York) with `python lavu_report.py`, or for a specific
business date with `python lavu_report.py --date YYYY-MM-DD`.
