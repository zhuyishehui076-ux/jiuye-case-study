"""T11 X3 report generator.

All numbers are collected through SQL first. Narrative paragraphs only format the
already computed metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from io import BytesIO

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.thresholds import load_employment_target_rate

SETTLED_STATUSES = ("签约", "升学", "出国", "入伍", "灵活就业")


@dataclass(frozen=True)
class ReportMetrics:
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    period_days: int
    total_students: int
    settled_students: int
    unsettled_students: int
    outcome_confirmed_students: int
    settlement_rate: float
    new_signings: int
    activity_count: int
    jobs_received: int
    risk_red: int
    risk_yellow: int
    open_assist_cases: int
    scope_name: str
    data_completeness_rate: float
    target_rate: float | None = None


def _scalar_int(db: Session, sql: str, params: dict | None = None) -> int:
    return int(db.execute(text(sql), params or {}).scalar() or 0)


def collect_report_metrics(
    db: Session,
    *,
    days: int,
    college_id: int | None = None,
) -> ReportMetrics:
    """Return a reconciliable school or counselor-college report scope."""
    generated_at = datetime.now(UTC)
    cutoff = db.execute(
        text("""
            SELECT max(value) FROM (
                SELECT max(status_occurred_at) AS value FROM students
                UNION ALL SELECT max(occurred_at) FROM job_actions
                UNION ALL SELECT max(held_at) FROM activities
                UNION ALL SELECT max(held_at) FROM fairs
            ) business_dates
        """)
    ).scalar() or generated_at
    start = cutoff - timedelta(days=days)
    params = {"start": start, "end": cutoff, "college_id": college_id}
    scope = "(CAST(:college_id AS INTEGER) IS NULL OR college_id = :college_id)"
    total = _scalar_int(db, f"SELECT count(*) FROM students WHERE {scope}", params)
    settled = _scalar_int(
        db,
        f"SELECT count(*) FROM students WHERE status = ANY(:statuses) AND {scope}",
        {**params, "statuses": list(SETTLED_STATUSES)},
    )
    outcome_confirmed = _scalar_int(
        db,
        f"SELECT count(*) FROM students "
        f"WHERE status <> '未落实' AND status_occurred_at IS NOT NULL AND {scope}",
        params,
    )
    new_signings = _scalar_int(
        db,
        """
        SELECT count(*)
        FROM job_actions ja
        JOIN students s ON s.id = ja.student_id
        WHERE ja.type = '签约'
          AND ja.occurred_at >= :start AND ja.occurred_at <= :end
          AND (CAST(:college_id AS INTEGER) IS NULL OR s.college_id = :college_id)
        """,
        params,
    )
    if college_id is None:
        activity_count = _scalar_int(
            db,
            "SELECT count(*) FROM activities "
            "WHERE held_at >= :start AND held_at <= :end",
            params,
        )
        jobs_received = _scalar_int(
            db,
            "SELECT count(*) FROM jobs "
            "WHERE received_at >= :start AND received_at <= :end",
            params,
        )
    else:
        activity_count = _scalar_int(
            db,
            "SELECT count(DISTINCT fc.fair_id) FROM fair_checkins fc "
            "JOIN students s ON s.id = fc.student_id "
            "WHERE s.college_id = :college_id "
            "AND fc.at >= :start AND fc.at <= :end",
            params,
        )
        jobs_received = _scalar_int(
            db,
            "SELECT count(DISTINCT m.job_id) FROM matches m "
            "JOIN students s ON s.id = m.student_id "
            "WHERE s.college_id = :college_id AND m.pushed_at IS NOT NULL "
            "AND m.pushed_at >= :start AND m.pushed_at <= :end",
            params,
        )
    risk_red = _scalar_int(
        db,
        f"SELECT count(*) FROM students WHERE risk_level = 'red' AND {scope}",
        params,
    )
    risk_yellow = _scalar_int(
        db,
        f"SELECT count(*) FROM students WHERE risk_level = 'yellow' AND {scope}",
        params,
    )
    open_assist = _scalar_int(
        db,
        "SELECT count(*) FROM assist_cases ac "
        "JOIN students s ON s.id = ac.student_id "
        "WHERE ac.status = '进行中' "
        "AND (CAST(:college_id AS INTEGER) IS NULL OR s.college_id = :college_id)",
        params,
    )
    complete = _scalar_int(
        db,
        f"SELECT count(*) FROM students WHERE {scope} "
        "AND nullif(trim(student_no), '') IS NOT NULL "
        "AND nullif(trim(name), '') IS NOT NULL "
        "AND college_id IS NOT NULL AND major_id IS NOT NULL "
        "AND cohort_year IS NOT NULL AND nullif(trim(status), '') IS NOT NULL",
        params,
    )
    scope_name = "全校"
    if college_id is not None:
        scope_name = str(
            db.execute(
                text("SELECT name FROM colleges WHERE id = :college_id"),
                {"college_id": college_id},
            ).scalar()
            or "本学院"
        )
    unsettled = max(total - settled, 0)
    configured_target_rate = load_employment_target_rate(db)
    return ReportMetrics(
        generated_at=generated_at,
        period_start=start,
        period_end=cutoff,
        period_days=days,
        total_students=total,
        settled_students=settled,
        unsettled_students=unsettled,
        outcome_confirmed_students=outcome_confirmed,
        settlement_rate=round(settled / total, 4) if total else 0.0,
        new_signings=new_signings,
        activity_count=activity_count,
        jobs_received=jobs_received,
        risk_red=risk_red,
        risk_yellow=risk_yellow,
        open_assist_cases=open_assist,
        scope_name=scope_name,
        data_completeness_rate=round(complete / total, 4) if total else 0.0,
        target_rate=(
            configured_target_rate / 100
            if configured_target_rate is not None
            else None
        ),
    )


def _period_text(metrics: ReportMetrics) -> str:
    start = metrics.period_start.strftime("%Y年%m月%d日")
    end = metrics.period_end.strftime("%Y年%m月%d日")
    return f"{start} 至 {end}"


def _generated_time(metrics: ReportMetrics) -> str:
    return metrics.generated_at.astimezone().strftime("%Y年%m月%d日 %H:%M")


def build_weekly_docx(metrics: ReportMetrics, audience: str = "leader_readonly") -> bytes:
    return _build_formal_docx(metrics, audience=audience, kind="weekly")


def build_monthly_xlsx(metrics: ReportMetrics) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "就业月报"
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A4"

    navy = "1F4E78"
    blue = "2E75B5"
    ink = "172132"
    muted = "5B6778"
    pale = "F2F5F9"
    line = Side(style="thin", color="D9E2F0")

    ws["A1"] = "就业月报"
    ws["A2"] = f"统计周期：{_period_text(metrics)}"
    ws.merge_cells("A1:B1")
    ws.merge_cells("A2:B2")
    ws["A1"].font = Font(name="微软雅黑", size=20, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor=navy)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws["A2"].font = Font(name="微软雅黑", size=10, color=muted)
    ws["A2"].fill = PatternFill("solid", fgColor=pale)
    ws["A2"].alignment = Alignment(horizontal="left", vertical="center")
    ws.append(["指标", "数值"])
    rows = [
        ("学生总数", metrics.total_students),
        ("已落实学生", metrics.settled_students),
        ("未落实学生", metrics.unsettled_students),
        ("已确认去向结果", metrics.outcome_confirmed_students),
        ("落实率", metrics.settlement_rate),
        ("近30天新增签约", metrics.new_signings),
        ("近30天活动场次", metrics.activity_count),
        ("近30天新增岗位", metrics.jobs_received),
        ("红色预警学生", metrics.risk_red),
        ("黄色预警学生", metrics.risk_yellow),
        ("进行中帮扶案例", metrics.open_assist_cases),
    ]
    for row in rows:
        ws.append(row)

    last_metric_row = ws.max_row
    for cell in ws[3]:
        cell.font = Font(name="微软雅黑", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=blue)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    for row_index in range(4, last_metric_row + 1):
        label = ws.cell(row_index, 1)
        value = ws.cell(row_index, 2)
        fill = "FFFFFF" if row_index % 2 == 0 else "F8FAFC"
        for cell in (label, value):
            cell.font = Font(name="微软雅黑", size=10, color=ink)
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.border = Border(bottom=line)
            cell.alignment = Alignment(vertical="center")
        label.alignment = Alignment(horizontal="left", vertical="center")
        value.alignment = Alignment(horizontal="right", vertical="center")
        value.number_format = "0.0%" if label.value == "落实率" else "#,##0"
        if label.value == "红色预警学生":
            label.fill = PatternFill("solid", fgColor="FCE8E6")
            label.font = Font(name="微软雅黑", size=10, bold=True, color="9B1C1C")
        elif label.value == "黄色预警学生":
            label.fill = PatternFill("solid", fgColor="FFF4CC")
            label.font = Font(name="微软雅黑", size=10, bold=True, color="7A5C00")

    ws.merge_cells(
        start_row=last_metric_row + 2,
        start_column=1,
        end_row=last_metric_row + 2,
        end_column=2,
    )
    ws.cell(last_metric_row + 2, 1).value = f"数据截止：{_generated_time(metrics)}"
    ws.merge_cells(
        start_row=last_metric_row + 3,
        start_column=1,
        end_row=last_metric_row + 3,
        end_column=2,
    )
    notice = ws.cell(last_metric_row + 3, 1)
    notice.value = "数据属性：全部为仓库内置模拟数据，仅供产品演示，不代表任何真实学校成绩。"
    for row_index in (last_metric_row + 2, last_metric_row + 3):
        cell = ws.cell(row_index, 1)
        cell.font = Font(
            name="微软雅黑",
            size=9,
            bold=row_index == last_metric_row + 3,
            color="9B1C1C" if row_index == last_metric_row + 3 else muted,
        )
        fill = "FFF1F0" if row_index == last_metric_row + 3 else pale
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)

    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 20
    ws.row_dimensions[1].height = 34
    ws.row_dimensions[2].height = 25
    ws.row_dimensions[3].height = 25
    for row_index in range(4, last_metric_row + 1):
        ws.row_dimensions[row_index].height = 23
    ws.row_dimensions[last_metric_row + 3].height = 34
    ws.auto_filter.ref = f"A3:B{last_metric_row}"
    ws.print_title_rows = "3:3"
    ws.print_area = f"A1:B{last_metric_row + 3}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.page_margins = PageMargins(left=0.6, right=0.6, top=0.7, bottom=0.7)
    ws.oddFooter.center.text = "模拟数据 · 仅供产品演示"
    ws.oddFooter.center.size = 9
    ws.oddFooter.center.color = "9B1C1C"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_material_docx(metrics: ReportMetrics, audience: str = "leader_readonly") -> bytes:
    return _build_formal_docx(metrics, audience=audience, kind="material")


_NAVY = RGBColor(31, 77, 120)
_BLUE = RGBColor(46, 116, 181)
_INK = RGBColor(23, 33, 50)
_MUTED = RGBColor(91, 103, 120)
_RED = RGBColor(155, 28, 28)


def _set_run_font(
    run,
    *,
    size: float = 11,
    bold: bool | None = None,
    color: RGBColor = _INK,
) -> None:
    run.font.name = "Microsoft YaHei"
    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "微软雅黑")
    run._element.get_or_add_rPr().rFonts.set(qn("w:ascii"), "Arial")
    run._element.get_or_add_rPr().rFonts.set(qn("w:hAnsi"), "Arial")
    run.font.size = Pt(size)
    run.font.color.rgb = color
    if bold is not None:
        run.bold = bold


def _configure_document(doc: Document) -> None:
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    section.top_margin = Inches(1)
    section.right_margin = Inches(1)
    section.bottom_margin = Inches(1)
    section.left_margin = Inches(1)
    section.header_distance = Inches(0.492)
    section.footer_distance = Inches(0.492)

    normal = doc.styles["Normal"]
    normal.font.name = "Microsoft YaHei"
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
    normal._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
    normal._element.rPr.rFonts.set(qn("w:hAnsi"), "Arial")
    normal.font.size = Pt(11)
    normal.font.color.rgb = _INK
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.1
    for name, size, color, before, after in (
        ("Heading 1", 16, _BLUE, 16, 8),
        ("Heading 2", 13, _BLUE, 12, 6),
        ("Heading 3", 12, _NAVY, 8, 4),
    ):
        style = doc.styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        style._element.rPr.rFonts.set(qn("w:ascii"), "Arial")
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = color
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
    _add_header_footer(section)


def _add_header_footer(section) -> None:
    header = section.header
    table = header.add_table(rows=1, cols=2, width=Inches(6.5))
    _set_table_geometry(table, [4680, 4680], indent=0)
    left = table.cell(0, 0).paragraphs[0]
    left.alignment = WD_ALIGN_PARAGRAPH.LEFT
    _set_run_font(left.add_run("启航就业｜校本就业系统"), size=8.5, bold=True, color=_MUTED)
    right = table.cell(0, 1).paragraphs[0]
    right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_run_font(right.add_run("模拟数据 · 仅供产品演示"), size=8.5, bold=True, color=_RED)
    _remove_table_borders(table)

    footer = section.footer
    table = footer.add_table(rows=1, cols=2, width=Inches(6.5))
    _set_table_geometry(table, [6000, 3360], indent=0)
    left = table.cell(0, 0).paragraphs[0]
    _set_run_font(left.add_run("数据口径以本报告末页说明为准"), size=8, color=_MUTED)
    right = table.cell(0, 1).paragraphs[0]
    right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _set_run_font(right.add_run("第 "), size=8, color=_MUTED)
    run = right.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, instr, separate, end])
    _set_run_font(right.add_run(" 页"), size=8, color=_MUTED)
    _remove_table_borders(table)


def _set_cell_margins(cell, *, top: int = 80, bottom: int = 80, start: int = 120, end: int = 120) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for side, value in (("top", top), ("bottom", bottom), ("start", start), ("end", end)):
        node = tc_mar.find(qn(f"w:{side}"))
        if node is None:
            node = OxmlElement(f"w:{side}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def _set_table_geometry(table, widths: list[int], *, indent: int = 120) -> None:
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.first_child_found_in("w:tblW")
    tbl_w.set(qn("w:w"), str(sum(widths)))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_ind = tbl_pr.first_child_found_in("w:tblInd")
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), str(indent))
    tbl_ind.set(qn("w:type"), "dxa")
    layout = tbl_pr.first_child_found_in("w:tblLayout")
    if layout is None:
        layout = OxmlElement("w:tblLayout")
        tbl_pr.append(layout)
    layout.set(qn("w:type"), "fixed")
    grid = table._tbl.tblGrid
    for child in list(grid):
        grid.remove(child)
    for width in widths:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            width = widths[min(index, len(widths) - 1)]
            tc_w = cell._tc.get_or_add_tcPr().get_or_add_tcW()
            tc_w.set(qn("w:w"), str(width))
            tc_w.set(qn("w:type"), "dxa")
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            _set_cell_margins(cell)
    if table.rows:
        tr_pr = table.rows[0]._tr.get_or_add_trPr()
        header = tr_pr.find(qn("w:tblHeader"))
        if header is None:
            header = OxmlElement("w:tblHeader")
            tr_pr.append(header)
        header.set(qn("w:val"), "true")


def _shade_cell(cell, fill: str) -> None:
    shd = cell._tc.get_or_add_tcPr().first_child_found_in("w:shd")
    if shd is None:
        shd = OxmlElement("w:shd")
        cell._tc.get_or_add_tcPr().append(shd)
    shd.set(qn("w:fill"), fill)


def _remove_table_borders(table) -> None:
    borders = table._tbl.tblPr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        table._tbl.tblPr.append(borders)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = borders.find(qn(f"w:{edge}"))
        if node is None:
            node = OxmlElement(f"w:{edge}")
            borders.append(node)
        node.set(qn("w:val"), "nil")


def _fill_table(table, rows: list[list[str]], widths: list[int]) -> None:
    _set_table_geometry(table, widths)
    for row_index, values in enumerate(rows):
        for col_index, value in enumerate(values):
            cell = table.cell(row_index, col_index)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.1
            run = paragraph.add_run(value)
            _set_run_font(
                run,
                size=9.5 if row_index else 9,
                bold=row_index == 0,
                color=_NAVY if row_index == 0 else _INK,
            )
            if row_index == 0:
                _shade_cell(cell, "F2F4F7")
            if col_index in {0, 1} and len(value) < 14:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER


def _role_title(audience: str, kind: str) -> str:
    titles = {
        "leader_readonly": "全校就业工作领导简报",
        "employment_office": "就业工作推进周报",
        "counselor": "学院学生帮扶周报",
        "admin": "系统数据质量检查报告",
    }
    title = titles.get(audience, "就业工作周报")
    if kind == "material" and audience != "admin":
        return title.replace("周报", "汇报材料").replace("简报", "汇报材料")
    return title


def _role_actions(metrics: ReportMetrics, audience: str) -> list[list[str]]:
    if audience == "admin":
        return [
            ["01", "核对未完整字段与异常口径", "系统管理员", "本周"],
            ["02", "复核运行日志、账号状态与审计记录", "系统管理员", "本周"],
            ["03", "将业务问题转交就业中心处理", "系统管理员", "持续"],
        ]
    if audience == "counselor":
        return [
            ["01", f"优先联系 {metrics.risk_red} 名红色预警学生", "学院辅导员", "2个工作日"],
            ["02", "补齐帮扶记录并定向推送适配岗位", "学院辅导员", "本周"],
            ["03", "跟进学生反馈并确认最终去向", "学院辅导员", "持续"],
        ]
    if audience == "employment_office":
        return [
            ["01", "按学院差距分派今天的优先跟进任务", "就业中心", "今日"],
            ["02", "补充岗位与招聘活动供给", "就业中心", "本周"],
            ["03", "复核推岗反馈与去向回流", "就业中心", "本周"],
        ]
    return [
        ["01", "督导目标差距较大的学院形成整改计划", "分管领导", "本周"],
        ["02", "听取就业中心闭环推进情况", "分管领导", "本周"],
        ["03", "关注红黄预警规模及结果回流", "分管领导", "持续"],
    ]


def _build_formal_docx(metrics: ReportMetrics, *, audience: str, kind: str) -> bytes:
    doc = Document()
    _configure_document(doc)

    kicker = doc.add_paragraph()
    kicker.paragraph_format.space_after = Pt(4)
    _set_run_font(
        kicker.add_run(f"启航就业 · {metrics.scope_name}就业工作"),
        size=10,
        bold=True,
        color=_BLUE,
    )
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(5)
    title.paragraph_format.keep_with_next = True
    _set_run_font(title.add_run(_role_title(audience, kind)), size=24, bold=True, color=_INK)
    subtitle = doc.add_paragraph()
    subtitle.paragraph_format.space_after = Pt(16)
    _set_run_font(
        subtitle.add_run(
            f"统计周期｜{_period_text(metrics)}    数据截止｜{metrics.period_end.strftime('%Y年%m月%d日 %H:%M')}"
        ),
        size=9.5,
        color=_MUTED,
    )

    callout = doc.add_table(rows=1, cols=1)
    _set_table_geometry(callout, [9360])
    _shade_cell(callout.cell(0, 0), "F4F6F9")
    paragraph = callout.cell(0, 0).paragraphs[0]
    paragraph.paragraph_format.space_after = Pt(0)
    if audience == "admin":
        lead = (
            f"本次检查覆盖 {metrics.total_students:,} 名模拟学生，完整度 "
            f"{metrics.data_completeness_rate:.1%}。管理员只负责数据启用、质量、账号与审计，"
            "不代替就业中心开展学生帮扶或岗位推送。"
        )
    else:
        lead = (
            f"当前去向落实率 {metrics.settlement_rate:.1%}，已落实 {metrics.settled_students:,} 人；"
            f"红黄预警共 {metrics.risk_red + metrics.risk_yellow:,} 人，"
            f"进行中帮扶 {metrics.open_assist_cases:,} 条。"
        )
    _set_run_font(paragraph.add_run(lead), size=10.5, bold=True, color=_NAVY)

    doc.add_heading("一、核心指标", level=1)
    metric_rows = [
        ["指标", "本期结果", "口径说明", "管理提示"],
        [
            "去向落实率",
            f"{metrics.settlement_rate:.1%}",
            "已落实人数 / 纳入统计人数",
            (
                f"目标值 {metrics.target_rate:.1%}"
                if metrics.target_rate is not None
                else "目标待就业中心配置"
            ),
        ],
        ["本期新增签约", f"{metrics.new_signings:,} 人", f"近 {metrics.period_days} 天签约事实", "核对结果回流"],
        ["重点跟进", f"{metrics.risk_red + metrics.risk_yellow:,} 人", "红色与黄色预警合计", "优先处理红色预警"],
        ["数据完整度", f"{metrics.data_completeness_rate:.1%}", "学号、姓名、学院、专业、届别、去向", "缺项须先补齐"],
    ]
    table = doc.add_table(rows=len(metric_rows), cols=4)
    _fill_table(table, metric_rows, [1800, 1500, 3300, 2760])

    doc.add_heading("二、本期工作进展", level=1)
    progress_rows = [
        ["工作事项", "数量", "当前判断"],
        ["就业活动", f"{metrics.activity_count:,} 场", "用于补充岗位供给与现场转化"],
        ["新增岗位", f"{metrics.jobs_received:,} 个", "进入岗位识别、推荐与反馈闭环"],
        ["进行中帮扶", f"{metrics.open_assist_cases:,} 条", "需持续记录行动、推岗、反馈和结果"],
        ["已确认去向", f"{metrics.outcome_confirmed_students:,} 人", "去向状态与发生时间均已登记"],
    ]
    table = doc.add_table(rows=len(progress_rows), cols=3)
    _fill_table(table, progress_rows, [2400, 1500, 5460])

    # 将行动计划作为第二页的起始内容，避免标题与表头滞留在第一页底部，
    # 而具体行动被拆到下一页。正式周报宁可使用清晰的逻辑分页，也不制造孤行。
    doc.add_page_break()
    doc.add_heading("三、下一步安排", level=1)
    action_rows = [["序号", "行动", "责任角色", "时限"], *_role_actions(metrics, audience)]
    table = doc.add_table(rows=len(action_rows), cols=4)
    _fill_table(table, action_rows, [900, 4680, 1980, 1800])

    doc.add_heading("四、统计口径与使用说明", level=1)
    scope_rows = [
        ["项目", "说明"],
        ["统计范围", f"{metrics.scope_name}，纳入统计 {metrics.total_students:,} 人"],
        ["去向落实", "签约、升学、出国、入伍、灵活就业"],
        ["数据截止", metrics.period_end.strftime("%Y年%m月%d日 %H:%M")],
        ["生成时间", _generated_time(metrics)],
        ["数据属性", "全部为仓库内置模拟数据，仅用于产品演示，不代表任何真实学校成绩"],
    ]
    table = doc.add_table(rows=len(scope_rows), cols=2)
    _fill_table(table, scope_rows, [1800, 7560])

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(8)
    note.paragraph_format.space_after = Pt(0)
    _set_run_font(
        note.add_run("统计口径：本报告数字来自同一模拟业务事实库，可与工作台、学生画像、帮扶台账和报表逐项对账。"),
        size=9,
        color=_MUTED,
    )
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
