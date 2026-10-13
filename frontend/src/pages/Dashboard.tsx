import { useEffect, useState } from "react";
import { Card, Col, Row, Statistic, Table, Tag, Empty, Spin } from "antd";
import type { TableColumnsType } from "antd";
import type { EChartsOption } from "echarts";
import { useNavigate } from "react-router-dom";
import { EChart } from "../components/EChart";
import { LIGHT_COLORS, CYAN } from "../theme";
import {
  fetchSummary,
  fetchColleges,
  fetchTrend,
  fetchAlerts,
  type DashboardSummary,
  type CollegeRow,
  type TrendPoint,
  type AlertRow,
} from "../api/client";

function LightPill({
  light,
  settlementRate,
}: {
  light: "red" | "yellow" | "green";
  settlementRate: number;
}) {
  const label = { red: "红", yellow: "黄", green: "绿" }[light];
  return (
    <span
      title={`落实率 ${settlementRate}%`}
      style={{
        display: "inline-block",
        minWidth: 34,
        textAlign: "center",
        padding: "2px 10px",
        borderRadius: 12,
        color: "#0a1a2f",
        fontWeight: 700,
        background: LIGHT_COLORS[light],
      }}
    >
      {label}
    </span>
  );
}

export function Dashboard() {
  const nav = useNavigate();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [colleges, setColleges] = useState<CollegeRow[]>([]);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([fetchSummary(), fetchColleges(), fetchTrend(), fetchAlerts()])
      .then(([s, c, t, a]) => {
        setSummary(s);
        setColleges(c);
        setTrend(t);
        setAlerts(a);
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin style={{ display: "block", margin: "120px auto" }} />;

  const trendOption: EChartsOption = {
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    grid: { left: 40, right: 20, top: 30, bottom: 30 },
    xAxis: { type: "category", data: trend.map((p) => p.month) },
    yAxis: { type: "value" },
    series: [
      {
        name: "签约数",
        type: "line",
        smooth: true,
        data: trend.map((p) => p.signings),
        areaStyle: { opacity: 0.15 },
        lineStyle: { color: CYAN, width: 2 },
        itemStyle: { color: CYAN },
      },
    ],
  };

  const donutOption: EChartsOption = {
    backgroundColor: "transparent",
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: "#cfe3f5" } },
    series: [
      {
        name: "去向结构",
        type: "pie",
        radius: ["45%", "70%"],
        label: { color: "#cfe3f5" },
        data: summary
          ? [
              { value: summary.signed_count, name: "签约" },
              { value: summary.further_study_count, name: "升学" },
              { value: summary.abroad_count, name: "出国" },
              { value: summary.military_count, name: "入伍" },
              { value: summary.flexible_count, name: "灵活就业" },
              { value: summary.not_seeking_count, name: "暂不就业" },
              { value: summary.unsettled_count, name: "未落实" },
            ].filter((d) => d.value > 0)
          : [],
      },
    ],
  };

  const collegeCols: TableColumnsType<CollegeRow> = [
    { title: "学院", dataIndex: "college_name", key: "college_name" },
    {
      title: "红绿灯",
      dataIndex: "settlement_rate",
      key: "light",
      render: (_: number, row) => (
        <LightPill light={row.light} settlementRate={row.settlement_rate} />
      ),
    },
    { title: "落实率%", dataIndex: "settlement_rate", key: "settlement_rate" },
    { title: "红", dataIndex: "red", key: "red" },
    { title: "黄", dataIndex: "yellow", key: "yellow" },
    { title: "绿", dataIndex: "green", key: "green" },
    { title: "人数", dataIndex: "total", key: "total" },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ color: CYAN, marginBottom: 20 }}>就业预警驾驶舱</h1>

      <Row gutter={16}>
        <Col span={6}>
          <Card>
            <Statistic title="全校落实率" value={summary?.settlement_rate ?? 0} suffix="%" valueStyle={{ color: CYAN }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="岗位供给比" value={summary?.supply_ratio ?? 0} valueStyle={{ color: CYAN }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="红色预警数" value={summary?.red_count ?? 0} valueStyle={{ color: LIGHT_COLORS.red }} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="本月活动数" value={summary?.recent_activities ?? 0} valueStyle={{ color: CYAN }} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={16}>
          <Card title="院系红绿灯">
            <Table
              rowKey="college_id"
              size="small"
              pagination={false}
              columns={collegeCols}
              dataSource={colleges}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card title="去向结构">
            <EChart option={donutOption} height={300} />
          </Card>
        </Col>
      </Row>

      <Row gutter={16} style={{ marginTop: 16 }}>
        <Col span={16}>
          <Card title="求职签约趋势">
            <EChart option={trendOption} height={280} />
          </Card>
        </Col>
        <Col span={8}>
          <Card title="预警动态流" styles={{ body: { maxHeight: 300, overflow: "auto" } }}>
            {alerts.length === 0 ? (
              <Empty />
            ) : (
              alerts.slice(0, 30).map((a) => (
                <div key={a.student_id} style={{ padding: "6px 0", borderBottom: "1px solid #1c3a5e" }}>
                  <Tag color={a.risk_level === "red" ? "red" : "gold"}>{a.risk_level === "red" ? "红" : "黄"}</Tag>
                  <button
                    type="button"
                    onClick={() => nav(`/profiles?student_id=${a.student_id}`)}
                    style={{
                      border: 0,
                      padding: 0,
                      background: "transparent",
                      color: CYAN,
                      cursor: "pointer",
                      font: "inherit",
                      fontWeight: 700,
                    }}
                  >
                    {a.name}
                  </button>
                  （{a.college_name}）· {a.status}
                  {a.risk_reason ? <div style={{ color: "#8aa7c4", fontSize: 12 }}>{a.risk_reason}</div> : null}
                </div>
              ))
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
