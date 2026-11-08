"""Build the research PDF body (ReportLab, TOC via TocDocTemplate + multiBuild),
then render cover (html2poster.js), merge, and run preflight.
English report - FreeSerif family, cascade palette (seed 11).
"""
import os, sys, hashlib, subprocess

PDF_SKILL_DIR = "/home/z/my-project/skills/pdf"
sys.path.insert(0, os.path.join(PDF_SKILL_DIR, "scripts"))

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase.pdfmetrics import registerFontFamily, stringWidth
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                                Table, TableStyle, Image, KeepTogether, CondPageBreak,
                                HRFlowable)
from reportlab.platypus.tableofcontents import TableOfContents
from PIL import Image as PILImage

# ---------------- fonts ----------------
FONT_DIR = "/usr/share/fonts"
pdfmetrics.registerFont(TTFont("NotoSerifSC", f"{FONT_DIR}/truetype/noto-serif-sc/NotoSerifSC-Regular.ttf"))
pdfmetrics.registerFont(TTFont("NotoSerifSC-Bold", f"{FONT_DIR}/truetype/noto-serif-sc/NotoSerifSC-Bold.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif", f"{FONT_DIR}/truetype/freefont/FreeSerif.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-Bold", f"{FONT_DIR}/truetype/freefont/FreeSerifBold.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-Italic", f"{FONT_DIR}/truetype/freefont/FreeSerifItalic.ttf"))
pdfmetrics.registerFont(TTFont("FreeSerif-BoldItalic", f"{FONT_DIR}/truetype/freefont/FreeSerifBoldItalic.ttf"))
pdfmetrics.registerFont(TTFont("DejaVuSans", f"{FONT_DIR}/truetype/dejavu/DejaVuSansMono.ttf"))
registerFontFamily("NotoSerifSC", normal="NotoSerifSC", bold="NotoSerifSC-Bold")
registerFontFamily("FreeSerif", normal="FreeSerif", bold="FreeSerif-Bold",
                   italic="FreeSerif-Italic", boldItalic="FreeSerif-BoldItalic")
registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans")
from pdf import install_font_fallback
install_font_fallback()

# ---------------- cascade palette (palette.cascade, seed 11) ----------------
PAGE_BG       = colors.HexColor('#f6f6f5')
SECTION_BG    = colors.HexColor('#efeeed')
CARD_BG       = colors.HexColor('#ebeae8')
TABLE_STRIPE  = colors.HexColor('#f0f0ef')
HEADER_FILL   = colors.HexColor('#6f6340')
COVER_BLOCK   = colors.HexColor('#6d6449')
BORDER        = colors.HexColor('#d2cdc0')
ICON          = colors.HexColor('#9c8646')
ACCENT        = colors.HexColor('#87702b')
ACCENT_2      = colors.HexColor('#7962bd')
TEXT_PRIMARY  = colors.HexColor('#1d1d1b')
TEXT_MUTED    = colors.HexColor('#8d8a83')
SEM_ERROR     = colors.HexColor('#8f4e48')

DATA = "/home/z/my-project/download/data"
OUTDIR = "/home/z/my-project/download"
BODY_PDF = f"{OUTDIR}/_body.pdf"
COVER_HTML = "/home/z/my-project/scripts/cover.html"
COVER_PDF = f"{OUTDIR}/_cover.pdf"
FINAL_PDF = f"{OUTDIR}/futures_prop_bot_research.pdf"

MARGIN = 0.9 * inch
PAGE_W, PAGE_H = A4
AVAIL_W = PAGE_W - 2 * MARGIN
AVAIL_H = PAGE_H - 2 * MARGIN

# ---------------- styles ----------------
body = ParagraphStyle('Body', fontName='FreeSerif', fontSize=10.5, leading=15.8,
                      alignment=TA_JUSTIFY, textColor=TEXT_PRIMARY, spaceAfter=8)
h1 = ParagraphStyle('H1', fontName='FreeSerif', fontSize=22, leading=27,
                    textColor=TEXT_PRIMARY, spaceBefore=18, spaceAfter=4)
h2 = ParagraphStyle('H2', fontName='FreeSerif', fontSize=15, leading=20,
                    textColor=HEADER_FILL, spaceBefore=12, spaceAfter=7)
h3 = ParagraphStyle('H3', fontName='FreeSerif', fontSize=11.5, leading=16,
                    textColor=TEXT_PRIMARY, spaceBefore=10, spaceAfter=6)
caption = ParagraphStyle('Caption', fontName='FreeSerif', fontSize=8.5, leading=12,
                         alignment=TA_CENTER, textColor=TEXT_MUTED)
bullet = ParagraphStyle('Bullet', fontName='FreeSerif', fontSize=10.5, leading=17,
                        alignment=TA_LEFT, textColor=TEXT_PRIMARY, leftIndent=14,
                        spaceAfter=5)
tbl_h = ParagraphStyle('TblH', fontName='FreeSerif', fontSize=8.6, leading=11.5,
                       alignment=TA_CENTER, textColor=colors.white)
tbl_c = ParagraphStyle('TblC', fontName='FreeSerif', fontSize=8.6, leading=11.5,
                       alignment=TA_CENTER, textColor=TEXT_PRIMARY)
tbl_l = ParagraphStyle('TblL', fontName='FreeSerif', fontSize=8.6, leading=11.5,
                       alignment=TA_LEFT, textColor=TEXT_PRIMARY)
stat_big = ParagraphStyle('StatBig', fontName='FreeSerif', fontSize=20, leading=24,
                          textColor=ACCENT, alignment=TA_CENTER)
stat_lbl = ParagraphStyle('StatLbl', fontName='FreeSerif', fontSize=8.5, leading=11,
                          textColor=TEXT_MUTED, alignment=TA_CENTER)
toc_l0 = ParagraphStyle('TOC0', fontName='FreeSerif', fontSize=12, leading=22, leftIndent=6)
toc_l1 = ParagraphStyle('TOC1', fontName='FreeSerif', fontSize=10.5, leading=18, leftIndent=26,
                        textColor=TEXT_MUTED)

class TocDocTemplate(SimpleDocTemplate):
    def afterFlowable(self, flowable):
        if hasattr(flowable, 'bookmark_name'):
            level = getattr(flowable, 'bookmark_level', 0)
            text = getattr(flowable, 'bookmark_text', '')
            key = getattr(flowable, 'bookmark_key', '')
            # report the footer-visible page number (TOC occupies internal page 1)
            self.notify('TOCEntry', (level, text, self.page - 1, key))

def add_heading(text, style, level=0):
    key = 'h_%s' % hashlib.md5(text.encode()).hexdigest()[:8]
    p = Paragraph('<a name="%s"/><b>%s</b>' % (key, text), style)
    p.bookmark_name = key
    p.bookmark_level = level
    p.bookmark_text = text
    p.bookmark_key = key
    return p

H1_THRESHOLD = AVAIL_H * 0.25

def section(story_list, num, title, threshold=None):
    story_list.append(CondPageBreak(threshold if threshold else H1_THRESHOLD))
    story_list.append(add_heading(f"{num}. {title}", h1, level=0))
    story_list.append(HRFlowable(width="100%", color=ACCENT, thickness=1.4,
                                 spaceBefore=2, spaceAfter=12))

def para(txt):
    story.append(Paragraph(txt, body))

def safe_keep_together(elements):
    total_h = 0
    for el in elements:
        w, hgt = el.wrap(AVAIL_W, AVAIL_H)
        total_h += hgt
    if total_h <= AVAIL_H * 0.4:
        return [KeepTogether(elements)]
    elif len(elements) >= 2:
        return [KeepTogether(elements[:2])] + list(elements[2:])
    return list(elements)

def embed_image(path, max_width=None, max_height=None):
    if max_width is None:
        max_width = AVAIL_W
    if max_height is None:
        max_height = A4[1] * 0.33
    im = PILImage.open(path)
    ow, oh = im.size
    rw = max_width / ow if ow > max_width else 1.0
    rh = max_height / oh if oh > max_height else 1.0
    r = min(rw, rh)
    return Image(path, width=ow * r, height=oh * r)

def figure(story_list, path, cap, max_h=250):
    img = embed_image(path, max_width=AVAIL_W, max_height=max_h)
    story_list.append(Spacer(1, 12))
    story_list.extend(safe_keep_together([img, Spacer(1, 6), Paragraph(cap, caption)]))
    story_list.append(Spacer(1, 12))

def make_table(headers, rows, ratios, left_cols=(0,)):
    assert abs(sum(ratios) - 1.0) < 1e-6
    col_w = [r * AVAIL_W for r in ratios]
    assert sum(col_w) <= AVAIL_W + 0.5
    data = [[Paragraph(f'<b>{h}</b>', tbl_h) for h in headers]]
    for row in rows:
        cells = []
        for j, v in enumerate(row):
            st = tbl_l if j in left_cols else tbl_c
            cells.append(Paragraph(str(v), st))
        data.append(cells)
    t = Table(data, colWidths=col_w, hAlign='CENTER', repeatRows=1)
    style = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_FILL),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for i in range(1, len(data)):
        style.append(('BACKGROUND', (0, i), (-1, i),
                      colors.white if i % 2 == 1 else TABLE_STRIPE))
    t.setStyle(TableStyle(style))
    return t

def table_block(story_list, tbl, cap):
    story_list.append(Spacer(1, 12))
    if cap:
        story_list.extend(safe_keep_together([tbl, Spacer(1, 6), Paragraph(cap, caption)]))
    else:
        story_list.append(tbl)
    story_list.append(Spacer(1, 12))

def callout_row(story_list, stats):
    """stats: list of (big, label) tuples, one row of callout boxes."""
    n = len(stats)
    gap = 10
    bw = (AVAIL_W - gap * (n - 1)) / n
    cells, widths = [], []
    for i, (big, lbl) in enumerate(stats):
        inner = Table([[Paragraph(f'<b>{big}</b>', stat_big)],
                       [Paragraph(lbl, stat_lbl)]], colWidths=[bw - 2])
        inner.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), CARD_BG),
            ('BOX', (0, 0), (-1, -1), 0.8, ACCENT),
            ('TOPPADDING', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, -1), (-1, -1), 9),
            ('TOPPADDING', (0, 1), (-1, 1), 1),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 1),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        cells.append(inner)
        widths.append(bw)
        if i < n - 1:
            cells.append('')
            widths.append(gap)
    outer = Table([cells], colWidths=widths, hAlign='CENTER')
    outer.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0),
                               ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                               ('TOPPADDING', (0, 0), (-1, -1), 0),
                               ('BOTTOMPADDING', (0, 0), (-1, -1), 0)]))
    story_list.append(Spacer(1, 8))
    story_list.append(outer)
    story_list.append(Spacer(1, 12))

# ---------------- header/footer ----------------
DOC_TITLE = "Prop Evaluation Bot Research"
def on_page(canvas, doc):
    canvas.saveState()
    # header
    canvas.setFont('FreeSerif', 7.5)
    canvas.setFillColor(TEXT_MUTED)
    canvas.drawString(MARGIN, PAGE_H - 0.55 * inch, DOC_TITLE)
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(1.2)
    canvas.line(MARGIN, PAGE_H - 0.62 * inch, PAGE_W - MARGIN, PAGE_H - 0.62 * inch)
    # footer
    canvas.setFont('FreeSerif', 7.5)
    canvas.setFillColor(TEXT_MUTED)
    canvas.drawString(MARGIN, 0.5 * inch, "November 2026")
    if doc.page == 1:
        canvas.drawCentredString(PAGE_W / 2, 0.5 * inch, "i")
    else:
        canvas.drawRightString(PAGE_W - MARGIN, 0.5 * inch, str(doc.page - 1))
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN, 0.62 * inch, PAGE_W - MARGIN, 0.62 * inch)
    canvas.restoreState()

# ================================================================ story
story = []

toc = TableOfContents()
toc.levelStyles = [toc_l0, toc_l1]
story.append(Paragraph('<b>Table of Contents</b>', ParagraphStyle(
    'TocTitle', parent=h1, spaceBefore=6, spaceAfter=14)))
story.append(toc)
story.append(PageBreak())

# ---------------- 1. Executive Summary ----------------
section(story, 1, "Executive Summary")
para("This research set out to answer a single practical question: using the same NT8-bridge "
     "architecture you already run live, can an automated strategy pass a futures prop-firm "
     "evaluation often enough, fast enough, and cheaply enough that the cash withdrawn from a "
     "funded account is meaningfully larger than everything paid in along the way? We fetched "
     "free historical data (no purchase required), built a bar-level backtester with realistic "
     "cost assumptions, tested three strategy archetypes on micro E-mini S&P (MES) and "
     "micro E-mini Nasdaq (MNQ), and then simulated the full 2026 rule sets of four major "
     "prop firms - Apex Trader Funding, Topstep, MyFundedFutures (MFFU) and Take Profit "
     "Trader (TPT) - with a 20,000-trial Monte Carlo that includes execution-degradation "
     "haircuts, consistency-rule enforcement and payout mechanics.")
callout_row(story, [
    ("51.5%", "TRADE WIN RATE<br/>(selected strategy, net of costs)"),
    ("80-96 days", "MEDIAN TIME TO FIRST<br/>WITHDRAWABLE PAYOUT"),
    ("$287", "EXPECTED PROGRAM FEES<br/>(Topstep 50K, base case)"),
    ("+$1,199", "EXPECTED NET CASH<br/>AFTER FIRST PAYOUT"),
])
para("Three findings drive the recommendation. First, of the three strategies tested, a "
     "session-VWAP fade (entering against 2-sigma extensions of the volume-weighted average "
     "price with a 1.2-sigma stop) was the only one with a robust, out-of-sample edge on both "
     "symbols; the popular opening-range breakout was marginal and an EMA pullback system was "
     "not reliably profitable. Second, because every firm we analyzed has moved to end-of-day "
     "trailing drawdowns in 2026, a bot that enforces a hard daily loss stop and a daily "
     "profit cap almost never breaches an evaluation - the binding constraints are speed "
     "(minimum trading days) and consistency caps, not ruin. Third, the economics differ "
     "sharply: Topstep 50K offers the best expected net cash (+$1,199 base, +$910 even with "
     "the edge cut by another 40%), while Apex 50K is the fastest and cheapest path to cash "
     "(56 days, $171 in fees) - provided you can comply with its semi-automated-trading-only "
     "policy. The full reasoning, data and risks follow.")
table_block(story, make_table(
    ["Path", "P(pass eval)", "Trade win %", "Median days to cash", "E[fees]", "E[first payout]", "E[net cash]"],
    [["Topstep 50K (recommended)", "100.0%", "51.5%", "80", "$287", "$1,487", "+$1,199"],
     ["Apex 50K (fastest)", "99.9%", "51.5%", "56", "$171", "$824", "+$653"],
     ["MFFU Rapid EOD 50K", "99.7%", "51.5%", "78", "$322", "$743", "+$428"],
     ["Topstep 100K (scale-up)", "100.0%", "51.5%", "93", "$446", "$2,117", "+$1,672"]],
    [0.24, 0.12, 0.11, 0.15, 0.11, 0.14, 0.13]),
    "Table 1: Headline Monte Carlo results, base scenario (backtest edge degraded by 40%). "
    "Fees = evaluation subscriptions + activation; commissions are netted inside P&L.")

# ---------------- 2. Data, Costs and Method ----------------
section(story, 2, "Data, Costs and Method")
story.append(add_heading("2.1 Free data and verification", h2, level=1))
para("All price data was fetched here, free of charge, from Yahoo Finance's public continuous "
     "futures series: daily bars back to 2000, hourly (1h) bars for 2.4 years (June 2024 to "
     "November 2026, roughly 600 regular trading hours sessions), and 15-minute plus "
     "5-minute bars for the most recent 50 sessions. We verified that ES and MES (and NQ and "
     "MNQ) are statistically the same series - correlation 1.00000 with a mean absolute "
     "difference of 0.001% - so signals are computed on the deeper ES/NQ data and executed "
     "at micro-contract specifications ($5 per point and $1.25 per tick for MES; $2 per point "
     "and $0.50 per tick for MNQ). Sessions around quarterly contract rolls with abnormal "
     "opening gaps were excluded to avoid artificial jumps, and every strategy is flat by "
     "15:59 ET, which satisfies both Apex's no-overnight rule and end-of-day drawdown "
     "accounting. No synthetic data was ultimately required, although the Monte Carlo's "
     "block bootstrap generates synthetic trading sequences from the real day distribution.")
story.append(add_heading("2.2 Execution and cost model", h2, level=1))
para("The backtester walks hourly bars and fills orders with deliberately conservative "
     "assumptions: stop-market entries fill at the trigger level or the bar open if gapped "
     "through; when both the stop and the target fall inside the same bar, the stop is "
     "assumed to execute first; and target fills require the price to trade through the "
     "level by at least one tick. Friction is charged per micro round turn: $1.20 of "
     "commission and exchange fees plus 0.5 ticks of slippage per side, which totals "
     "$2.45 for MES and $1.70 for MNQ. Because hourly bars cannot see every adverse "
     "intrabar path, the Monte Carlo additionally degrades the backtest edge: the base "
     "scenario keeps 60% of backtest expectancy and the stress scenario keeps 35%, each "
     "with added per-day execution noise. The bot risk layer is identical across firms: "
     "a daily loss stop ($400 on 50K accounts, $600 on 100K), position sizing that scales "
     "from 2 to 4 micros per symbol (50K) or 3 to 6 (100K) once 50% of the profit target "
     "is reached, and daily profit caps that enforce each firm's consistency rule.")
table_block(story, make_table(
    ["Item", "Value used", "Note"],
    [["Commission + fees (micro RT)", "$1.20", "Tradovate/Rithmic-class pricing, both sides"],
     ["Slippage", "0.5 tick per side", "$1.25 RT on MES, $0.50 RT on MNQ"],
     ["Market data", "$0", "Free Yahoo data; firms bundle Level 1 in evals and funded accounts"],
     ["Trading window", "10:00-15:59 ET", "Flat by 15:59; hourly bars, top-of-hour aligned"],
     ["Daily loss stop", "$400 (50K) / $600 (100K)", "Bot circuit breaker, below every firm's DLL"],
     ["Sizing ladder", "2 to 4 micros (50K); 3 to 6 (100K)", "Per symbol, scale at 50% of target"],
     ["Trades per day", "~2.5 across both symbols", "Mean on traded days"]],
    [0.30, 0.26, 0.44]),
    "Table 2: Execution and cost assumptions applied uniformly across all firms and scenarios.")
figure(story, f"{DATA}/chart_equity.png",
       "Figure 1: Selected strategy (VWAP fade, variant 1) portfolio equity per 1 micro MES + 1 micro MNQ, "
       "net of all costs, June 2024 to November 2026.", 235)

# ---------------- 3. Strategy Research ----------------
section(story, 3, "Strategy Research and Selection")
para("Three archetypes were implemented and tested with an in-sample/out-of-sample split "
     "(everything before March 1, 2026 is in-sample; the final eight months are out-of-sample "
     "and were not used for any design choice). The <b>10:00 range breakout</b> buys or sells "
     "the break of the first clean trading hour with a stop at the opposite side of the range "
     "and a 1.5x or 2.0x target. The <b>VWAP fade</b> waits for price to stretch 1.5-2.0 "
     "standard deviations from the session's volume-weighted average price, enters against "
     "the move, targets a return to VWAP and protects with a 1.0-1.2 sigma stop; a 0.6% "
     "session-move filter skips strongly trending days, and at most three trades are taken "
     "per day. The <b>EMA pullback</b> trades continuations of a 9/21 EMA trend on pullback "
     "touches, with ATR-based stop and target. All three run identically on both symbols.")
table_block(story, make_table(
    ["Strategy", "Sym", "IS trades", "IS win %", "IS exp $", "OOS trades", "OOS win %", "OOS exp $", "OOS PF"],
    [["VWAP fade v1 (k=2.0)", "ES", "405", "51.1", "+18.33", "175", "48.6", "+14.59", "1.83"],
     ["VWAP fade v1 (k=2.0)", "NQ", "340", "53.8", "+37.65", "126", "50.8", "+47.58", "2.59"],
     ["VWAP fade v0 (k=1.5)", "ES", "534", "47.8", "+12.39", "227", "43.6", "+7.24", "1.40"],
     ["VWAP fade v0 (k=1.5)", "NQ", "453", "51.4", "+28.30", "177", "49.2", "+35.67", "2.17"],
     ["10:00 breakout v1 (2R)", "ES", "260", "45.4", "+0.95", "82", "51.2", "+8.91", "1.21"],
     ["10:00 breakout v1 (2R)", "NQ", "162", "43.2", "-3.72", "23", "43.5", "+10.52", "1.11"],
     ["10:00 breakout v0 (1.5R)", "ES", "260", "46.5", "+2.14", "82", "51.2", "+2.05", "1.05"],
     ["10:00 breakout v0 (1.5R)", "NQ", "162", "45.1", "-4.17", "23", "43.5", "-4.24", "0.96"],
     ["EMA pullback v1", "ES", "220", "51.8", "+0.64", "80", "52.5", "+3.05", "1.06"],
     ["EMA pullback v1", "NQ", "205", "43.9", "-4.69", "69", "50.7", "-0.23", "1.00"],
     ["EMA pullback v0", "ES", "226", "47.3", "-6.21", "82", "50.0", "+1.41", "1.03"],
     ["EMA pullback v0", "NQ", "211", "40.8", "-11.81", "69", "50.7", "+14.47", "1.14"]],
    [0.24, 0.07, 0.09, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10]),
    "Table 3: In-sample (IS, Jun 2024 - Feb 2026) vs out-of-sample (OOS, Mar - Nov 2026) results per "
    "micro contract, net of commissions and slippage. Expectancy in dollars per trade.")
para("The VWAP fade is the clear selection: it is the only archetype positive in both "
     "in-sample and out-of-sample on both symbols, with a profit factor between 1.4 and 2.6 "
     "out-of-sample. Its structure also fits prop rules unusually well - a 2-sigma entry "
     "with a 1.2-sigma stop yields roughly a 1.7:1 reward-to-risk with near-50% wins, "
     "profit arrives in frequent modest daily increments rather than one spectacular day, "
     "and the trend filter removes the days where fading bleeds. The 10:00 breakout, "
     "despite its popularity, earns almost exactly its friction cost on ES and loses on NQ "
     "in-sample; the EMA pullback is not reliably profitable at this granularity. We also "
     "re-ran the VWAP fade on the unseen 15-minute bars of the last 50 sessions as a "
     "final robustness check: ES earned $8.14 per trade (PF 1.35) and NQ $39.63 (PF 2.00), "
     "confirming that the edge is not an artifact of hourly aggregation.")
figure(story, f"{DATA}/chart_strategies.png",
       "Figure 2: Out-of-sample net expectancy per trade by strategy variant and symbol. "
       "VWAP fade v1 is the selected production configuration.", 250)

# ---------------- 4. Prop Firm Landscape ----------------
section(story, 4, "The 2026 Prop Firm Landscape")
para("The industry converged during 2025-2026 in a way that matters enormously for bot "
     "designers: evaluations now overwhelmingly use <b>end-of-day trailing drawdowns</b> "
     "rather than the older intraday-trailing style. An EOD floor only follows your closing "
     "balance, so a bot that limits how much it can lose in a day (our $400-600 daily stop) "
     "structurally cannot breach a $2,000-3,000 floor in one session. The binding "
     "constraints shift to minimum trading days (Apex 7, TPT 5, MFFU Rapid EOD 4, Topstep 2), "
     "consistency rules that cap any single day's profit, and - for one-time-fee firms - "
     "30-day evaluation windows that expire progress. Apex in particular restructured in "
     "March 2026 to one-time evaluation purchases with no resets and no monthly fee, which "
     "makes repeated failed attempts cheap but each attempt a fresh 30-day race.")
table_block(story, make_table(
    ["Rule (Nov 2026)", "Apex 50K/100K", "Topstep 50K/100K", "MFFU 50K/100K", "TPT 50K/100K"],
    [["Eval cost", "$33 / $67 one-time (promo)", "$49 / $99 first mo, then $75 / $119", "$125 / $214 one-time (promo)", "$170 / $330 monthly"],
     ["Activation fee", "$99 / $137", "$149 / $149", "$0 / $0", "$130 / $130"],
     ["Profit target", "$3,000 / $6,000", "$3,000 / $6,000", "$3,000 / $6,000", "$3,000 / $6,000"],
     ["Drawdown (eval)", "$2,500 / $3,000 EOD trail", "$2,000 / $3,000 EOD trail", "$2,000 / $3,000 EOD trail", "$2,000 / $3,000 EOD trail"],
     ["Min trading days", "7 / 7", "2 / 2", "4 / 2", "5 / 5"],
     ["Eval consistency", "none / none", "best day < 55% of target", "30% / 50%", "50% / 50%"],
     ["Daily loss limit", "none", "$1,000 / $2,000 (fixed at checkout)", "none", "none"],
     ["Eval window", "30 calendar days, no resets", "unlimited (monthly rebill, 1 free reset)", "30 days, no resets", "unlimited (monthly)"],
     ["Funded payouts", "weekly, 5 qualifying days; caps $1.5k-$4k; 100% split", "5 win days ≥ $150; caps $2k/$4k and $3k/$6k", "daily, $500 min, 90/10, buffer $2.1k/$3.1k", "day-one, 80% above $52k/$103k balance"],
     ["Max micros", "100 / 140", "50 / 100", "30 / 80", "60 / 120"]],
    [0.16, 0.21, 0.23, 0.20, 0.20]),
    "Table 4: Evaluation rule cheat-sheet as verified in November 2026. Promotional pricing varies "
    "week to week; confirm at checkout. MFFU 100K = Rapid plan (Rapid EOD exists only in 25K/50K).")
story.append(add_heading("4.1 Automation policy - critical for an NT8 bot", h2, level=1))
para("Because your execution path is the NinjaTrader 8 bridge, each firm's automation "
     "policy is not a footnote - it is a compliance constraint that can void payouts. "
     "Apex updated its policy in March 2026 to permit <b>semi-automated trading only</b>: "
     "strategies may fire signals and orders, but a human must actively supervise, and "
     "set-and-forget operation plus copy-trading between accounts are prohibited. Topstep "
     "allows expert advisors and copy trading on its supported platforms with restrictions "
     "on high-frequency and latency-arbitrage behaviour. Take Profit Trader connects "
     "natively to NinjaTrader and Tradovate and automation is generally tolerated, and "
     "MyFundedFutures' terms should be re-confirmed at signup since plans changed in "
     "August 2026. In practice the bot should run supervised every session, which the "
     "deployment plan in Section 7 accounts for.")
table_block(story, make_table(
    ["Firm", "Bots / EAs", "Copy trading", "What the NT8 bot must do"],
    [["Apex", "Semi-auto only (Mar 2026 policy)", "Prohibited between accounts", "Keep a human supervisor active each session; no unattended multi-account mirroring"],
     ["Topstep", "Allowed with restrictions", "Allowed", "Avoid HFT-style behaviour; respect the checkout-fixed daily loss limit"],
     ["MFFU", "Permitted - verify current ToS", "Verify", "Re-read plan terms at signup (plans changed Aug 2026)"],
     ["TPT", "Generally permitted - verify ToS", "Verify", "Native NT8/Tradovate support; confirm bot clause with support"]],
    [0.10, 0.28, 0.20, 0.42]),
    "Table 5: Automation and copy-trading policies relevant to NT8-bridge execution.")

# ---------------- 5. Evaluation Monte Carlo ----------------
section(story, 5, "Evaluation Monte Carlo")
para("To convert a backtest into the three numbers you asked for, we simulate each firm's "
     "full journey 20,000 times. Each trial bootstraps trading days (in five-day blocks, to "
     "preserve regime persistence) from the strategy's real day-P&L distribution, applies "
     "the degradation haircut, and walks the account through the evaluation stage by stage: "
     "EOD trailing floor updates, minimum trading days, consistency caps (Topstep best-day "
     "below 55% of target; MFFU 30%/50%; TPT 50%), daily loss stops, 30-day windows with "
     "repurchase for one-time-fee firms, and monthly rebilling with one free reset for "
     "Topstep. Trials that pass pay the activation fee and continue into the funded stage, "
     "where we model payout qualification (Apex 5 x $50+ days with an 8-trading-day gap; "
     "Topstep 5 winning days of $150+; MFFU/TPT buffers of $2,100-$3,100 above start), "
     "payout caps, split percentages, floor locks (including Topstep's reset of the loss "
     "floor to the starting balance after the first payout) and processing delays. "
     "Program fees, activation fees and monthly renewals accumulate into the total cost.")
table_block(story, make_table(
    ["Firm", "P(pass)", "E[att]", "E[eval days]", "E[days to cash]", "90th pct", "E[fees]", "E[payout]", "E[net cash]"],
    [["Apex 50K", "99.9%", "2.19", "33", "56", "70", "$171", "$824", "+$653"],
     ["Apex 100K", "94.0%", "4.08", "36", "57", "67", "$434", "$1,025", "+$681"],
     ["Topstep 50K", "100.0%", "1.00", "51", "83", "116", "$287", "$1,487", "+$1,199"],
     ["Topstep 100K", "100.0%", "1.00", "65", "96", "131", "$446", "$2,117", "+$1,672"],
     ["MFFU Rapid EOD 50K", "99.7%", "2.54", "34", "82", "113", "$322", "$743", "+$428"],
     ["MFFU Rapid 100K", "92.6%", "4.10", "36", "86", "117", "$1,003", "$789", "-$25"],
     ["TPT 50K", "100.0%", "1.00", "52", "99", "142", "$510", "$741", "+$232"],
     ["TPT 100K", "100.0%", "1.00", "66", "116", "162", "$1,019", "$842", "-$177"]],
    [0.185, 0.095, 0.08, 0.115, 0.135, 0.09, 0.10, 0.10, 0.10]),
    "Table 6: Base scenario (edge haircut x0.60), 20,000 trials per row. Days are calendar days; "
    "E[fees] includes evaluation purchases, monthly renewals during the evaluation and activation.")
table_block(story, make_table(
    ["Firm", "P(pass)", "E[att]", "E[days to cash]", "E[fees]", "E[payout]", "E[net cash]"],
    [["Apex 50K", "81.2%", "4.91", "67", "$286", "$582", "+$455"],
     ["Apex 100K", "31.3%", "6.12", "64", "$723", "$281", "+$349"],
     ["Topstep 50K", "100.0%", "1.00", "131", "$373", "$1,283", "+$910"],
     ["Topstep 100K", "100.0%", "1.00", "148", "$622", "$1,544", "+$923"],
     ["MFFU Rapid EOD 50K", "66.6%", "5.46", "116", "$956", "$479", "+$37"],
     ["MFFU Rapid 100K", "27.8%", "6.23", "122", "$2,225", "$220", "-$538"],
     ["TPT 50K", "100.0%", "1.00", "168", "$709", "$717", "+$9"],
     ["TPT 100K", "100.0%", "1.00", "195", "$1,509", "$774", "-$734"]],
    [0.22, 0.12, 0.11, 0.17, 0.13, 0.13, 0.12]),
    "Table 7: Stress scenario (edge haircut x0.35). The strategy still pays at 50K on the "
    "EOD-drawdown firms, but 100K one-time-fee programs stop being viable.")
figure(story, f"{DATA}/chart_firms.png",
       "Figure 3: Expected days to first payout (left) and expected net cash at first payout "
       "(right), base scenario. Topstep maximizes net cash; Apex minimizes time.", 250)

# ---------------- 6. Results ----------------
section(story, 6, "Results: Win %, Timeframe and Cost")
para("<b>Win %.</b> The selected strategy wins 51.5% of its trades net of full friction "
     "(48.6% on ES and 50.8% on NQ out-of-sample), with 61.7% of trading days finishing "
     "green. At the account level - the number that actually pays you - the evaluation pass "
     "rate is 99.7-100% across the recommended 50K programs in the base scenario, and "
     "81-100% in the stress scenario where the live edge is assumed to be just a third of "
     "the backtest. The probability of eventually receiving a first payout equals the pass "
     "rate for monthly firms (no time limit) and is marginally lower for one-time-fee "
     "firms only because a 12-attempt cap was imposed in simulation.")
para("<b>Timeframe.</b> Decomposed, the journey to your first withdrawable cash is: "
     "evaluation trading (30-65 calendar days depending on firm and account size, driven "
     "by targets, minimum-day rules and consistency caps), then funded-stage qualification "
     "(5-15 sessions to satisfy payout conditions or buffers), plus processing (1-5 trading "
     "days). Medians from the base scenario: Apex 50K 56 days, MFFU 50K 78 days, Topstep "
     "50K 80 days, Topstep 100K 93 days. The 90th percentile is roughly 40-50% longer; "
     "plan for about three to five months door-to-door on Topstep and six to eight weeks "
     "on Apex in normal conditions.")
para("<b>Cost.</b> Expected program fees to first payout range from $171 (Apex 50K, "
     "cheap-but-repeating evaluations) through $287 (Topstep 50K) to $1,019 (TPT 100K). "
     "Commissions consumed while trading to that point add roughly $190-680 depending on "
     "the path (already netted inside the P&L). Against that, the expected first payout "
     "is $743-$2,117, so the expected net position at the moment your first withdrawal "
     "lands is positive for every 50K program except TPT (+$232) and negative for the "
     "100K versions of MFFU Rapid (-$25) and TPT (-$177), where fat buffers and 80-90% "
     "splits arrive too slowly relative to fee burn.")
figure(story, f"{DATA}/chart_dist.png",
       "Figure 4: Monte Carlo distributions of days to first payout (left) and first payout "
       "size (right), base scenario, for the three 50K paths.", 195)

# ---------------- 7. Recommendation ----------------
section(story, 7, "Recommendation and Deployment Plan")
para("<b>Primary path: Topstep 50K.</b> It pairs the highest expected net cash (+$1,199 "
     "base, +$910 stress) with the most forgiving failure economics - an unlimited "
     "evaluation window, a $1,000 daily loss limit that our $400 stop sits well inside, "
     "one free reset per rebill, and a 55%-of-target consistency ceiling that our bot's "
     "own daily profit caps respect automatically. Its two structural caveats are the "
     "$149 activation and the fact that after your first payout the maximum-loss floor "
     "resets to your starting balance, so the funded account must be managed more "
     "conservatively after cash-out (withdraw at the $2,000 cap and re-trade at reduced "
     "size the following week). <b>Speed option: Apex 50K</b> "
     "reaches cash a month sooner for a third of the fees, at the price of 30-day "
     "windows, a 7-day minimum, and the semi-auto-only supervision requirement. "
     "<b>Avoid for this strategy:</b> TPT and MFFU at 100K - their buffers and splits "
     "are tuned for larger per-trade edges and their fee burn dominates.")
_h71 = add_heading("7.1 Bot specification (NT8)", h2, level=1)
_t8 = make_table(
    ["Parameter", "Value", "Rationale"],
    [["Entry", "Fade limit orders at VWAP +/- 2.0 session sigma", "Selected on IS; confirmed OOS and on 15m data"],
     ["Stop", "1.2 sigma from entry (stop-market)", "Yields ~1.7:1 reward-to-risk at ~50% wins"],
     ["Target", "Session VWAP touch", "Natural magnet level; frequent modest wins fit consistency caps"],
     ["Trend filter", "Skip new fades when |session move| > 0.6%", "Removes trend days where fades bleed"],
     ["Trade cap", "Max 3 trades/day/symbol; one position at a time", "Limits fee burn and daily variance"],
     ["Session", "Signals 10:00-14:00 ET, flat by 15:59 ET", "Top-of-hour data alignment; no overnight exposure"],
     ["Daily stop", "Halt for the day at -$400 (50K)", "Bots cannot breach a $2,000 EOD floor in one session"],
     ["Sizing", "2 micros/symbol; 4 after 50% of target", "Balanced ladder; respects 50-100 micro limits"],
     ["Daily profit cap", "min(day P&L, firm consistency cap)", "e.g. $1,650 on Topstep 50K (55% of $3,000)"],
     ["Supervision", "Human monitors every session (Apex requirement)", "Semi-auto compliance; also a sanity check on fills"]],
    [0.17, 0.37, 0.46])
story.extend(safe_keep_together([_h71, _t8, Spacer(1, 6),
    Paragraph("Table 8: Production bot specification. Identical logic on MES and MNQ; params are those "
              "of VWAP fade variant 1.", caption)]))
story.append(Spacer(1, 12))
story.append(add_heading("7.2 Launch sequence", h2, level=1))
para("Run the strategy in NT8 Market Replay and then on a single sim account for two "
     "weeks, verifying that VWAP-target limit orders actually fill on touch-through. Buy one Topstep 50K "
     "with the daily loss limit selected at checkout, run the bot supervised, and treat "
     "the first evaluation as a live validation of the degradation haircut: if 20 "
     "traded days produce an average day better than the stress scenario's, the model's "
     "confidence intervals apply as published. On passing, pay activation, trade the "
     "funded account to the first payout at the same size, withdraw at the cap, and only "
     "then consider scaling - a second Topstep account or an Apex 50K running the same "
     "logic (supervised; no Apex copy-trading). Re-verify firm rules "
     "monthly; every parameter in Table 4 changed at least once during 2025-2026.")

# ---------------- 8. Limitations ----------------
section(story, 8, "Limitations and Risks", threshold=0.55 * AVAIL_H)
para("Honest limits of this research. <b>Backtest realism:</b> hourly bars cannot resolve "
     "every intrabar sequence, so even conservative fill models (stop-first, trade-through "
     "targets, 0.5-tick slippage) can be beaten by live fills; the base and stress haircuts "
     "absorb this, but they are assumptions, not guarantees, and a two-week Market Replay "
     "shakedown is the cheapest insurance before real evaluation fees are at stake. "
     "<b>Regime coverage:</b> the hourly history spans June "
     "2024 to November 2026 - a high-volatility, trending period; a multi-year range-bound "
     "regime would change the VWAP fade's profile, which is exactly why position sizes stay "
     "small and the daily stop stays hard. <b>Bootstrap assumptions:</b> block bootstrap preserves short-range persistence but "
     "cannot foresee rule changes or a firm simply refusing a payout, and payout caps mean "
     "large funded profits take several weekly cycles to fully withdraw. "
     "<b>Rule volatility:</b> every firm revised rules and prices repeatedly during 2025-2026 "
     "(Apex's March 2026 one-time restructure and MFFU's August 2026 plan changes are two "
     "recent examples); re-confirm every fee and rule at purchase. <b>Compliance:</b> Apex's semi-auto-only policy makes "
     "unattended operation a payout risk; Topstep's post-payout floor reset concentrates "
     "bust risk after you withdraw. Finally, this document is a quantitative research "
     "exercise built on simulated evaluations and free historical data - it is not "
     "financial advice, futures trading carries substantial risk of loss, and past or "
     "simulated performance never guarantees future results.")

# ================================================================ build body
doc = TocDocTemplate(BODY_PDF, pagesize=A4,
                     leftMargin=MARGIN, rightMargin=MARGIN,
                     topMargin=MARGIN, bottomMargin=MARGIN,
                     title=DOC_TITLE, author="Z.ai", creator="Z.ai",
                     subject="Backtest and Monte Carlo research: automated VWAP-fade bot on MES/MNQ vs 2026 futures prop firm rules")
doc.multiBuild(story, onFirstPage=on_page, onLaterPages=on_page)
print("body built:", BODY_PDF)
