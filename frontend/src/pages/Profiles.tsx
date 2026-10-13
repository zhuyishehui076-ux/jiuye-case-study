import { useEffect, useMemo, useState } from "react";
import { Button, Card, Col, Empty, List, Row, Select, Space, Spin, Table, Tag } from "antd";
import type { TableColumnsType } from "antd";
import type { EChartsOption } from "echarts";
import { useSearchParams } from "react-router-dom";
import { EChart } from "../components/EChart";
import { CYAN } from "../theme";
import {
  fetchProfileDetail,
  fetchProfileStudents,
  type ProfileDetail,
  type ProfileEvidenceClaim,
  type ProfileEvidenceSource,
  type ProfileListItem,
} from "../api/client";

const TERM = "2024-2025-1";
const DIM_COLORS: Record<string, string> = {
  专业成绩: "#13c2c2",
  沟通表达: "#52c41a",
  实践能力: "#faad14",
  求职主动性: "#ff4d4f",
  科研竞赛: "#9254de",
  职业清晰度: "#40a9ff",
};
const RISK_META: Record<string, { label: string; color: string; order: number }> = {
  red: { label: "红", color: "#ff4d4f", order: 0 },
  yellow: { label: "黄", color: "#faad14", order: 1 },
  green: { label: "绿", color: "#52c41a", order: 2 },
};

function riskMeta(level: string) {
  return RISK_META[level] ?? { label: level || "-", color: "#8aa7c4", order: 9 };
}

function sortProfileRows(data: ProfileListItem[]) {
  return [...data].sort((a, b) => {
    const riskDiff = riskMeta(a.risk_level).order - riskMeta(b.risk_level).order;
    if (riskDiff !== 0) return riskDiff;
    return (a.student_no || "").localeCompare(b.student_no || "");
  });
}

function sourceMap(sources: ProfileEvidenceSource[]) {
  return new Map(sources.map((s) => [s.id, s]));
}

function EvidencePanel({
  claim,
  sources,
}: {
  claim: ProfileEvidenceClaim | null;
  sources: Map<string, ProfileEvidenceSource>;
}) {
  if (!claim) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  const rows = claim.evidence_ids.map((id) => sources.get(id)).filter(Boolean) as ProfileEvidenceSource[];
  return (
    <List
      size="small"
      dataSource={rows}
      renderItem={(row) => (
        <List.Item>
          <List.Item.Meta
            title={<Space><Tag color="cyan">{row.id}</Tag><span>{row.type}</span></Space>}
            description={
              <div>
                <div>{row.summary}</div>
                {row.value ? (
                  <pre style={{ whiteSpace: "pre-wrap", margin: "6px 0 0", color: "#9fc7e8" }}>
                    {JSON.stringify(row.value, null, 2)}
                  </pre>
                ) : null}
              </div>
            }
          />
        </List.Item>
      )}
    />
  );
}

export function Profiles() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [rows, setRows] = useState<ProfileListItem[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProfileDetail | null>(null);
  const [selectedClaim, setSelectedClaim] = useState<ProfileEvidenceClaim | null>(null);
  const [loading, setLoading] = useState(true);

  const loadRows = () => fetchProfileStudents().then((data) => {
    const sorted = sortProfileRows(data);
    const requestedId = Number(searchParams.get("student_id"));
    const requestedExists = sorted.some((row) => row.student_id === requestedId);
    setRows(sorted);
    setSelectedId((current) => {
      if (requestedExists) return requestedId;
      if (current && sorted.some((row) => row.student_id === current)) return current;
      return sorted[0]?.student_id ?? null;
    });
  });

  useEffect(() => {
    loadRows().finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const requestedId = Number(searchParams.get("student_id"));
    if (requestedId && rows.some((row) => row.student_id === requestedId)) {
      setSelectedId(requestedId);
    }
  }, [rows, searchParams]);

  useEffect(() => {
    if (!selectedId) return;
    fetchProfileDetail(selectedId)
      .then((data) => {
        setDetail(data);
        setSelectedClaim(data.evidence.claims[0] ?? null);
      })
      .catch(() => {
        setDetail(null);
        setSelectedClaim(null);
      });
  }, [selectedId]);

  const sources = useMemo(
    () => sourceMap(detail?.evidence.sources ?? []),
    [detail],
  );

  const radarOption: EChartsOption = useMemo(() => {
    const dims = Object.entries(detail?.six_dims ?? {});
    return {
      backgroundColor: "transparent",
      tooltip: {},
      radar: {
        indicator: dims.map(([name]) => ({ name, max: 100 })),
        radius: "68%",
        axisName: { color: "#cfe3f5" },
      },
      series: [{
        type: "radar",
        data: [{
          value: dims.map(([, value]) => value),
          name: detail?.name ?? "画像",
          areaStyle: { opacity: 0.2, color: CYAN },
          lineStyle: { color: CYAN, width: 2 },
          itemStyle: { color: CYAN },
        }],
      }],
    };
  }, [detail]);

  const columns: TableColumnsType<ProfileListItem> = [
    {
      title: "预警",
      dataIndex: "risk_level",
      key: "risk_level",
      width: 76,
      render: (level: string) => {
        const meta = riskMeta(level);
        return (
          <Space size={6}>
            <span
              aria-label={`risk-${level}`}
              style={{
                width: 9,
                height: 9,
                borderRadius: "50%",
                background: meta.color,
                boxShadow: `0 0 8px ${meta.color}`,
                display: "inline-block",
              }}
            />
            <span>{meta.label}</span>
          </Space>
        );
      },
    },
    { title: "学号", dataIndex: "student_no", key: "student_no", width: 120 },
    { title: "姓名", dataIndex: "name", key: "name", width: 90 },
    { title: "学院", dataIndex: "college_name", key: "college_name" },
    { title: "专业", dataIndex: "major_name", key: "major_name" },
    { title: "去向", dataIndex: "status", key: "status", width: 90 },
    {
      title: "均分",
      dataIndex: "score_avg",
      key: "score_avg",
      width: 90,
      render: (v: number | null) => v ?? "-",
    },
  ];

  if (loading) return <Spin style={{ display: "block", margin: "120px auto" }} />;

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 style={{ color: CYAN, margin: 0 }}>学生数字画像</h1>
        <Space>
          <Select
            style={{ width: 150 }}
            value={detail?.term ?? TERM}
            options={(detail?.terms.length ? detail.terms : [TERM]).map((term) => ({
              label: term,
              value: term,
            }))}
            onChange={async (term) => {
              if (!selectedId) return;
              const data = await fetchProfileDetail(selectedId, term);
              setDetail(data);
              setSelectedClaim(data.evidence.claims[0] ?? null);
            }}
          />
          <Button
            type="primary"
            disabled
            title="D1 演示使用预计算研判，避免覆盖已生成结果。"
          >
            重建本学期
          </Button>
        </Space>
      </Space>

      <Row gutter={16}>
        <Col span={10}>
          <Card title="学生列表">
            <Table
              size="small"
              rowKey="student_id"
              columns={columns}
              dataSource={rows}
              pagination={{ pageSize: 8 }}
              rowClassName={(row) => row.student_id === selectedId ? "ant-table-row-selected" : ""}
              onRow={(row) => ({
                onClick: () => {
                  setSelectedId(row.student_id);
                  setSearchParams({ student_id: String(row.student_id) });
                },
              })}
            />
          </Card>
        </Col>
        <Col span={14}>
          {detail ? (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Card
                title={`${detail.name} · ${detail.major_name}`}
                extra={<Tag color={detail.llm_meta.status === "generated" ? "green" : "gold"}>{String(detail.llm_meta.status ?? "unknown")}</Tag>}
              >
                <Row gutter={16}>
                  <Col span={12}>
                    <EChart option={radarOption} height={320} />
                  </Col>
                  <Col span={12}>
                    <Space wrap>
                      {Object.entries(detail.six_dims).map(([name, value]) => (
                        <Tag key={name} color={DIM_COLORS[name] ?? "blue"}>{name} {value}</Tag>
                      ))}
                    </Space>
                    <p style={{ marginTop: 18, color: "#d8eefb", lineHeight: 1.8 }}>
                      {detail.narrative_text}
                    </p>
                    <List
                      size="small"
                      header="建议"
                      dataSource={detail.evidence.suggestions}
                      renderItem={(item) => <List.Item>{item}</List.Item>}
                    />
                  </Col>
                </Row>
              </Card>

              <Row gutter={16}>
                <Col span={10}>
                  <Card title="结论">
                    <List
                      size="small"
                      dataSource={detail.evidence.claims}
                      renderItem={(claim) => (
                        <List.Item
                          style={{ cursor: "pointer" }}
                          onClick={() => setSelectedClaim(claim)}
                        >
                          <Space direction="vertical" size={2}>
                            <span style={{ color: selectedClaim?.id === claim.id ? CYAN : "#d8eefb" }}>
                              {claim.text}
                            </span>
                            <Space wrap>
                              {claim.evidence_ids.map((id) => <Tag key={id}>{id}</Tag>)}
                            </Space>
                          </Space>
                        </List.Item>
                      )}
                    />
                  </Card>
                </Col>
                <Col span={14}>
                  <Card title="证据来源">
                    <EvidencePanel claim={selectedClaim} sources={sources} />
                  </Card>
                </Col>
              </Row>
            </Space>
          ) : (
            <Card>
              <Empty description="暂无画像" />
            </Card>
          )}
        </Col>
      </Row>
    </div>
  );
}
