# VWAP Fade paper-trading diary (engine v2)

Diary restarted 2026-11-05 during remediation: the v1 diary mixed replay and
live lines (audit H6) and is archived under archive/pre_remediation_2026-11-05/.
Engine v2 writes replay output to replay/ only; live diary lines carry unique
run delimiters and journal rows carry unique trade_ids.

- --- live run 2026-11-05T11:20:29.168658-05:00 ---
- --- live run 2026-11-05T12:20:18.891620-05:00 ---
- --- live run 2026-11-05T12:40:20.271492-05:00 ---
- --- live run 2026-11-05T14:40:18.388938-05:00 ---
- --- live run 2026-11-05T15:40:16.725948-05:00 ---
- --- live run 2026-11-05T16:20:16.675881-05:00 ---
**DAY SUMMARY 2026-11-05** — trades: 0, day P&L: $+0.00, cumulative: $+0.00 (+0% of $3,000 target)
- Topstep 50K: profit +0/3000, floor -2000, tdays 0, best day $+0, fees $0, attempts 1
- Apex 50K: profit +0/3000, floor -2500, tdays 0, best day $+0, fees $0, attempts 1
- --- live run 2026-11-05T16:40:16.982979-05:00 ---
- --- live run 2026-11-06T10:00:23.497213-05:00 ---
- --- live run 2026-11-06T10:40:19.770049-05:00 ---
- --- live run 2026-11-06T11:20:16.983815-05:00 ---
- --- live run 2026-11-06T13:20:25.150241-05:00 ---
- `live` 11-06 13:20 ET **ES 2 micro LONG opened** signal 7405.53 (12:00 bar) -> filled 7416.75 @ 13:20 (poll_quote, lag 20m; stop 7397.83, target 7437.06, sd 15.76, id 2026-11-06_ES_01)
- --- live run 2026-11-06T14:00:21.087378-05:00 ---
- --- live run 2026-11-06T16:40:34.460787-05:00 ---
**DAY SUMMARY 2026-11-06** — trades: 1, day P&L: $-194.06, cumulative: $-194.06 (-6% of $3,000 target)
- Topstep 50K: profit -194/3000, floor -2000, tdays 1, best day $+0, fees $0, attempts 1
- Apex 50K: profit -194/3000, floor -2500, tdays 1, best day $+0, fees $0, attempts 1
- `live` 11-06 15:00 ET **ES 2 micro LONG closed (stop)** 7416.75 -> 7397.83 = $-194.06 (day -194.06, cum $-194.06)
