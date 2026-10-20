"""
Reusable export helpers for Excel (.xlsx) and PDF.
Drop this file anywhere and import directly — no Django app dependency.

    from path.to.export_helpers import build_excel_response, build_pdf_response
"""
from django.http import HttpResponse
from django.utils import timezone
import math
from collections import Counter
from datetime import date

NAVY  = '1E3A5F'
BLUE  = '2563EB'
ALT   = 'F1F5F9'
WHITE = 'FFFFFF'
REDBG = 'FEE2E2'
SLATE = '334155'
LIGHT = 'BFDBFE'


def build_export_chart_data(rows, category_index, date_index, category_title):
    category_counts = Counter()
    daily_counts = Counter()

    for row in rows:
        if category_index < len(row):
            category = str(row[category_index]).strip()
            if category and category not in {'—', '-', 'N/A'}:
                category_counts[category] += 1

        if date_index is not None and date_index < len(row):
            date_value = str(row[date_index]).strip()[:10]
            try:
                day = date.fromisoformat(date_value).isoformat()
            except ValueError:
                continue
            daily_counts[day] += 1

    return {
        'category': sorted(category_counts.items()),
        'category_title': category_title,
        'daily': sorted(daily_counts.items()),
    }


def build_excel_response(filename_base, sheet_title, headers, col_widths,
                         rows, highlight_fn=None, mono_cols=None,
                         center_cols=None, summary_rows=None,
                         date_from='', date_to='', chart_data=None):
    """
    Returns an HttpResponse with an .xlsx attachment.
    rows: list of lists (first column should be row #)
    highlight_fn(row) -> bool  — red background when True
    mono_cols / center_cols: 1-based sets of column indices
    summary_rows: list of (label, value) for a second Summary sheet
    chart_data: optional category and per-day counts for the Summary sheet charts
    """
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    mono_cols   = mono_cols   or set()
    center_cols = center_cols or set()

    thin = Side(style='thin', color='CBD5E1')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    left   = Alignment(horizontal='left',   vertical='center', wrap_text=True)

    def fill(hex_): return PatternFill('solid', fgColor=hex_)
    def font(bold=False, size=9, color=SLATE, name='Calibri'):
        return Font(name=name, bold=bold, size=size, color=color)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_title
    ncols = len(headers)
    last = get_column_letter(ncols)

    # ── Title ──
    ws.merge_cells(f'A1:{last}1')
    c = ws['A1']
    c.value = sheet_title.upper()
    c.font = Font(name='Calibri', bold=True, size=14, color=WHITE)
    c.fill = fill(NAVY); c.alignment = center; c.border = border
    ws.row_dimensions[1].height = 28

    # ── Meta ──
    ws.merge_cells(f'A2:{last}2')
    c = ws['A2']
    date_label = ''
    if date_from and date_to:   date_label = f'  |  Period: {date_from} – {date_to}'
    elif date_from:             date_label = f'  |  From: {date_from}'
    elif date_to:               date_label = f'  |  To: {date_to}'
    c.value = f'Generated: {timezone.localdate()}  |  Total: {len(rows)}{date_label}'
    c.font = Font(name='Calibri', size=9, color=LIGHT)
    c.fill = fill(NAVY); c.alignment = center
    ws.row_dimensions[2].height = 14
    ws.row_dimensions[3].height = 5  # spacer

    # ── Column headers ──
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=4, column=i, value=h)
        c.font = Font(name='Calibri', bold=True, size=10, color=WHITE)
        c.fill = fill(BLUE); c.alignment = center; c.border = border
    ws.row_dimensions[4].height = 20

    # Start with requested widths, then auto-expand based on headers and content.
    computed_widths = {}
    for c_idx, header in enumerate(headers, 1):
        requested_width = float(col_widths[c_idx - 1]) if c_idx <= len(col_widths) else 8.0
        header_width = max((len(line) for line in str(header).splitlines()), default=0) + 2.0
        computed_widths[c_idx] = min(60.0, max(8.0, requested_width, header_width))

    for row in rows:
        for c_idx, val in enumerate(row, 1):
            text = '' if val is None else str(val)
            longest_line = max((len(line) for line in text.splitlines()), default=0)
            computed_widths[c_idx] = max(
                computed_widths.get(c_idx, 8.0),
                min(60.0, longest_line + 2.0),
            )

    header_lines = 1
    for c_idx, header in enumerate(headers, 1):
        text = str(header)
        longest_line = max((len(line) for line in text.splitlines()), default=0)
        col_width = max(computed_widths.get(c_idx, 8.0), 8.0)
        wrapped_lines = max(1, math.ceil(max(1, longest_line) / max(8.0, col_width - 1.5)))
        header_lines = max(header_lines, wrapped_lines, text.count('\n') + 1)
    ws.row_dimensions[4].height = min(60, max(20, 15 * header_lines))

    # ── Data rows ──
    for r_idx, row in enumerate(rows, 1):
        erow = r_idx + 4
        flagged = highlight_fn(row) if highlight_fn else False
        bg = fill(REDBG) if flagged else (fill(ALT) if r_idx % 2 == 0 else fill(WHITE))
        max_line_estimate = 1
        for c_idx, val in enumerate(row, 1):
            cell = ws.cell(row=erow, column=c_idx, value=val)
            cell.fill = bg; cell.border = border
            cell.alignment = center if c_idx in center_cols else left

            text = '' if val is None else str(val)
            longest_line = max((len(line) for line in text.splitlines()), default=0)
            col_width = max(computed_widths.get(c_idx, 8.0), 8.0)
            wrapped_lines = max(1, math.ceil(max(1, longest_line) / max(8.0, col_width - 1.5)))
            explicit_lines = max(1, text.count('\n') + 1)
            max_line_estimate = max(max_line_estimate, wrapped_lines, explicit_lines)

            if c_idx == 1:
                cell.font = Font(name='Calibri', size=8, color='94A3B8')
            elif c_idx in mono_cols:
                cell.font = Font(name='Courier New', size=9, bold=True,
                                 color='DC2626' if flagged else NAVY)
            else:
                cell.font = Font(name='Calibri', size=9,
                                 color='DC2626' if flagged else SLATE)
        ws.row_dimensions[erow].height = min(80, max(15, 15 * max_line_estimate))

    for c_idx, width in computed_widths.items():
        ws.column_dimensions[get_column_letter(c_idx)].width = width
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.print_title_rows = '1:4'

    if rows:
        ws.freeze_panes = 'A5'
        ws.auto_filter.ref = f'A4:{last}{4 + len(rows)}'

    # ── Summary sheet ──
    if summary_rows or chart_data is not None:
        ws2 = wb.create_sheet('Summary')
        ws2.column_dimensions['A'].width = 28
        ws2.column_dimensions['B'].width = 16
        ws2.merge_cells('A1:B1')
        c = ws2['A1']
        c.value = 'SUMMARY'
        c.font = Font(name='Calibri', bold=True, size=12, color=WHITE)
        c.fill = fill(NAVY); c.alignment = center
        ws2.row_dimensions[1].height = 22
        for i, (label, value) in enumerate(summary_rows or [], 2):
            bg = fill(ALT) if i % 2 == 0 else fill(WHITE)
            lc = ws2.cell(row=i, column=1, value=label)
            vc = ws2.cell(row=i, column=2, value=value)
            lc.fill = vc.fill = bg
            lc.font = Font(name='Calibri', size=10, color=SLATE)
            vc.font = Font(name='Calibri', size=10, bold=True, color=NAVY)
            lc.alignment = left; vc.alignment = center
            lc.border = vc.border = border
            ws2.row_dimensions[i].height = 18

        if chart_data is not None:
            from openpyxl.chart import BarChart, PieChart, Reference

            def add_chart_or_na(title, values, anchor, chart_type):
                if values:
                    start_row = max(25, len(summary_rows or []) + 3)
                    data_col = 1 if chart_type == 'pie' else 4
                    label_col = data_col + 1
                    ws2.cell(row=start_row, column=data_col, value=title)
                    ws2.cell(row=start_row, column=label_col, value='Records')
                    for offset, (label, value) in enumerate(values, 1):
                        ws2.cell(row=start_row + offset, column=data_col, value=label)
                        ws2.cell(row=start_row + offset, column=label_col, value=value)

                    data = Reference(
                        ws2,
                        min_col=label_col,
                        min_row=start_row,
                        max_row=start_row + len(values),
                    )
                    categories = Reference(
                        ws2,
                        min_col=data_col,
                        min_row=start_row + 1,
                        max_row=start_row + len(values),
                    )
                    if chart_type == 'pie':
                        chart = PieChart()
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(categories)
                        chart.height = 8
                        chart.width = 13
                        chart.style = 10
                    else:
                        chart = BarChart()
                        chart.type = 'col'
                        chart.style = 10
                        chart.title = title
                        chart.y_axis.title = 'Records'
                        chart.x_axis.title = 'Date'
                        chart.add_data(data, titles_from_data=True)
                        chart.set_categories(categories)
                        chart.height = 8
                        chart.width = 18
                        chart.legend = None
                        chart.x_axis.tickLblSkip = max(1, math.ceil(len(values) / 12))
                    if chart_type == 'pie':
                        chart.title = title
                    ws2.add_chart(chart, anchor)
                else:
                    cell = ws2[anchor]
                    cell.value = f'{title}: N/A'
                    cell.font = Font(name='Calibri', bold=True, size=12, color=SLATE)
                    cell.alignment = center

            add_chart_or_na(
                chart_data.get('category_title', 'Category Breakdown'),
                chart_data.get('category', []),
                'D2',
                'pie',
            )
            add_chart_or_na(
                'Records per Day',
                chart_data.get('daily', []),
                'L2',
                'bar',
            )

    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    resp['Content-Disposition'] = f'attachment; filename="{filename_base}_{timezone.localdate()}.xlsx"'
    wb.save(resp)
    return resp


def build_pdf_response(filename_base, report_title, headers, col_widths_mm,
                       rows, highlight_fn=None, mono_col_idx=None,
                       date_from='', date_to='', chart_data=None):
    """
    Returns an HttpResponse with a landscape A4 PDF attachment.
    col_widths_mm: list of mm values matching headers length.
    chart_data: optional category and per-day counts rendered before the report table.
    """
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing, String
    from xml.sax.saxutils import escape

    resp = HttpResponse(content_type='application/pdf')
    resp['Content-Disposition'] = (
        f'attachment; filename="{filename_base}_{timezone.localdate()}.pdf"'
    )
    doc = SimpleDocTemplate(resp, pagesize=landscape(A4),
                            leftMargin=12*mm, rightMargin=12*mm,
                            topMargin=12*mm, bottomMargin=12*mm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('T', parent=styles['Title'], fontSize=15,
                                 textColor=colors.HexColor(f'#{NAVY}'), spaceAfter=2)
    sub_style   = ParagraphStyle('S', parent=styles['Normal'], fontSize=8,
                                 textColor=colors.HexColor('#64748B'))

    date_label = ''
    if date_from and date_to:   date_label = f' | Period: {date_from} – {date_to}'
    elif date_from:             date_label = f' | From: {date_from}'
    elif date_to:               date_label = f' | To: {date_to}'

    elems = [
        Paragraph(report_title, title_style),
        Paragraph(f'Generated: {timezone.localdate()}  |  Total: {len(rows)}{date_label}', sub_style),
        Spacer(1, 5*mm),
    ]

    if chart_data is not None:
        chart_width = (landscape(A4)[0] - 24*mm) / 2
        chart_height = 160
        chart_colors = [
            colors.HexColor('#2563EB'),
            colors.HexColor('#10B981'),
            colors.HexColor('#F59E0B'),
            colors.HexColor('#8B5CF6'),
        ]

        def make_chart(title, values, is_pie=False):
            drawing = Drawing(chart_width, chart_height)
            drawing.add(String(
                8,
                chart_height - 14,
                title,
                fontName='Helvetica-Bold',
                fontSize=9,
                fillColor=colors.HexColor(f'#{NAVY}'),
            ))
            if not values:
                drawing.add(String(
                    chart_width / 2,
                    chart_height / 2,
                    'N/A',
                    fontName='Helvetica-Bold',
                    fontSize=18,
                    textAnchor='middle',
                    fillColor=colors.HexColor('#64748B'),
                ))
                return drawing

            chart_data_values = [value for _, value in values]
            chart_labels = [label for label, _ in values]
            if is_pie:
                chart = Pie()
                chart.x = (chart_width - 140) / 2
                chart.y = 18
                chart.width = 140
                chart.height = 120
                chart.data = chart_data_values
                chart.labels = chart_labels
                chart.slices.strokeWidth = 0.5
                for index, _ in enumerate(chart_data_values):
                    chart.slices[index].fillColor = chart_colors[index % len(chart_colors)]
                drawing.add(chart)
            else:
                chart = VerticalBarChart()
                chart.x = 38
                chart.y = 40
                chart.width = chart_width - 50
                chart.height = chart_height - 68
                chart.data = [chart_data_values]
                chart.categoryAxis.categoryNames = [
                    label if len(chart_labels) <= 14 or index % math.ceil(len(chart_labels) / 14) == 0 else ''
                    for index, label in enumerate(chart_labels)
                ]
                chart.categoryAxis.labels.fontName = 'Helvetica'
                chart.categoryAxis.labels.fontSize = 6
                chart.categoryAxis.labels.angle = 35
                chart.categoryAxis.labels.dy = -4
                chart.valueAxis.valueMin = 0
                chart.valueAxis.valueMax = max(chart_data_values) or 1
                chart.valueAxis.valueStep = max(1, math.ceil(max(chart_data_values) / 5))
                chart.valueAxis.labels.fontName = 'Helvetica'
                chart.valueAxis.labels.fontSize = 6
                chart.bars[0].fillColor = chart_colors[0]
                chart.barSpacing = 2
                drawing.add(chart)
            return drawing

        charts_table = Table(
            [[
                make_chart(
                    chart_data.get('category_title', 'Category Breakdown'),
                    chart_data.get('category', []),
                    is_pie=True,
                ),
                make_chart('Records per Day', chart_data.get('daily', [])),
            ]],
            colWidths=[chart_width, chart_width],
        )
        charts_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        elems.extend([charts_table, Spacer(1, 5*mm)])

    available_width = landscape(A4)[0] - doc.leftMargin - doc.rightMargin
    column_widths = []
    for index, header in enumerate(headers):
        requested_width = float(col_widths_mm[index]) if index < len(col_widths_mm) else 1.0
        content_width = max(
            stringWidth(line, 'Helvetica', 7)
            for value in [header] + [row[index] if index < len(row) else '' for row in rows]
            for line in str(value if value is not None else '').splitlines() or ['']
        ) / mm + 4
        column_widths.append(max(requested_width, min(80.0, content_width)))
    total_column_width = sum(column_widths) or 1.0
    column_widths_pts = [width * available_width / total_column_width for width in column_widths]

    header_style = ParagraphStyle(
        'ExportTableHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.white,
        wordWrap='CJK',
    )
    cell_style = ParagraphStyle(
        'ExportTableCell',
        parent=styles['Normal'],
        fontSize=7,
        leading=9,
        wordWrap='CJK',
    )
    highlighted_cell_style = ParagraphStyle(
        'ExportTableHighlightedCell',
        parent=cell_style,
        textColor=colors.HexColor('#DC2626'),
    )
    mono_cell_style = ParagraphStyle(
        'ExportTableMonoCell',
        parent=cell_style,
        fontName='Courier-Bold',
    )
    highlighted_mono_cell_style = ParagraphStyle(
        'ExportTableHighlightedMonoCell',
        parent=mono_cell_style,
        textColor=colors.HexColor('#DC2626'),
    )

    def table_cell(value, style):
        text = '' if value is None else str(value)
        return Paragraph(escape(text).replace('\n', '<br/>'), style)

    data = [[table_cell(header, header_style) for header in headers]]
    highlighted_rows = []
    for row in rows:
        flagged = highlight_fn(row) if highlight_fn else False
        highlighted_rows.append(flagged)
        row_cells = []
        for index, value in enumerate(row):
            if index == mono_col_idx:
                row_style = highlighted_mono_cell_style if flagged else mono_cell_style
            else:
                row_style = highlighted_cell_style if flagged else cell_style
            row_cells.append(table_cell(value, row_style))
        data.append(row_cells)
    table = Table(data, colWidths=column_widths_pts, repeatRows=1)

    style_cmds = [
        ('BACKGROUND',    (0,0), (-1,0), colors.HexColor(f'#{NAVY}')),
        ('TEXTCOLOR',     (0,0), (-1,0), colors.white),
        ('FONTNAME',      (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',      (0,0), (-1,0), 8),
        ('FONTSIZE',      (0,1), (-1,-1), 7),
        ('ROWBACKGROUNDS',(0,1), (-1,-1), [colors.white, colors.HexColor(f'#{ALT}')]),
        ('GRID',          (0,0), (-1,-1), 0.3, colors.HexColor('#CBD5E1')),
        ('VALIGN',        (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING',    (0,0), (-1,-1), 3),
        ('BOTTOMPADDING', (0,0), (-1,-1), 3),
        ('ALIGN',         (0,0), (-1,0),  'CENTER'),
    ]
    if highlight_fn:
        for i, flagged in enumerate(highlighted_rows, 1):
            if flagged:
                style_cmds += [
                    ('BACKGROUND', (0,i), (-1,i), colors.HexColor(f'#{REDBG}')),
                    ('TEXTCOLOR',  (0,i), (-1,i), colors.HexColor('#DC2626')),
                ]
    if mono_col_idx is not None:
        style_cmds.append(('FONTNAME', (mono_col_idx,1), (mono_col_idx,-1), 'Courier-Bold'))

    table.setStyle(TableStyle(style_cmds))
    elems.append(table)
    doc.build(elems)
    return resp