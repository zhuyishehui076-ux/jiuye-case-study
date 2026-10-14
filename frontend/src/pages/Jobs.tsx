import { useEffect, useState } from "react";
import { Button, Card, Col, Form, Input, InputNumber, Row, Space, Table, Upload, message } from "antd";
import type { TableColumnsType } from "antd";
import {
  calculateMatches,
  exportMatches,
  fetchMatches,
  fetchReviewQueue,
  importJobsXlsx,
  parseJob,
  pasteJob,
  pushMatches,
  reviewJobParse,
  type MatchRow,
  type MatchExportLanguage,
  type ReviewQueueItem,
} from "../api/client";
import { CYAN } from "../theme";

function csvList(value?: string) {
  return (value ?? "")
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);
}

const SAMPLE_JOB = {
  company: "启航智造科技有限公司",
  title: "智能制造软件工程师",
  raw_text:
    "工作地点：上海。岗位职责：负责制造执行系统、设备数据采集与生产看板开发，参与工业软件需求分析和现场交付。任职要求：本科及以上，计算机科学与技术、软件工程、自动化等相关专业，熟悉 Java/Python/SQL，具备数据分析或工业互联网项目经验优先。",
};

export function Jobs() {
  const [reviewQueue, setReviewQueue] = useState<ReviewQueueItem[]>([]);
  const [selectedReview, setSelectedReview] = useState<ReviewQueueItem | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [matches, setMatches] = useState<MatchRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [pasteForm] = Form.useForm();
  const [reviewForm] = Form.useForm();

  const downloadMatches = async (language: MatchExportLanguage) => {
    if (!jobId) return;
    try {
      const blob = await exportMatches(jobId, language);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `job-${jobId}-matches-${language}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      message.error("名单导出失败，请稍后重试。");
    }
  };

  const loadReviewQueue = () => fetchReviewQueue().then((data) => {
    setReviewQueue(data);
    return data;
  });

  useEffect(() => {
    loadReviewQueue().then((data) => {
      const first = data[0];
      if (!first) return;
      setJobId(first.job_id);
      fetchMatches(first.job_id).then(setMatches).catch(() => setMatches([]));
    });
  }, []);

  const matchCols: TableColumnsType<MatchRow> = [
    { title: "学号", dataIndex: "student_no", key: "student_no", width: 120 },
    { title: "姓名", dataIndex: "student_name", key: "student_name", width: 100 },
    { title: "学院", dataIndex: "college_id", key: "college_id", width: 80 },
    { title: "分数", dataIndex: "score", key: "score", width: 90 },
    { title: "依据", dataIndex: "reason_text", key: "reason_text" },
  ];

  const reviewCols: TableColumnsType<ReviewQueueItem> = [
    { title: "岗位", dataIndex: "title", key: "title" },
    { title: "企业", dataIndex: "company", key: "company" },
    { title: "置信度", dataIndex: "parse_confidence", key: "parse_confidence", width: 90 },
    { title: "原因", dataIndex: "review_reason", key: "review_reason" },
    {
      title: "操作",
      key: "action",
      width: 90,
      render: (_, row) => (
        <Button size="small" onClick={() => {
          setSelectedReview(row);
          reviewForm.setFieldsValue({
            degree_req: "本科",
            major_codes: "",
            direction_keywords: "",
            location: "",
          });
        }}>
          复核
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ color: CYAN, marginBottom: 20 }}>岗位解析与智能匹配</h1>

      <Row gutter={16}>
        <Col span={12}>
          <Card title="岗位入库">
            <Form
              form={pasteForm}
              layout="vertical"
              onFinish={async (values) => {
                setLoading(true);
                try {
                  const job = await pasteJob(values);
                  setJobId(job.id);
                  const parsed = await parseJob(job.id);
                  await loadReviewQueue();
                  setMatches(await calculateMatches(job.id));
                  pasteForm.resetFields();
                  message.success(
                    parsed.needs_review
                      ? `已入库并解析 #${job.id}，进入复核队列`
                      : `已入库并解析 #${job.id}`,
                  );
                } finally {
                  setLoading(false);
                }
              }}
            >
              <Form.Item name="company" label="企业" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="title" label="岗位" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
              <Form.Item name="raw_text" label="JD 原文" rules={[{ required: true }]}>
                <Input.TextArea rows={5} />
              </Form.Item>
              <Space>
                <Button onClick={() => pasteForm.setFieldsValue(SAMPLE_JOB)}>
                  载入示例JD
                </Button>
                <Button type="primary" htmlType="submit" loading={loading}>
                  入库并解析
                </Button>
                <Upload
                  accept=".xlsx"
                  showUploadList={false}
                  beforeUpload={async (file) => {
                    const rows = await importJobsXlsx(file);
                    message.success(`已导入 ${rows.length} 条`);
                    return false;
                  }}
                >
                  <Button>导入 xlsx</Button>
                </Upload>
              </Space>
            </Form>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="复核队列">
            <Table
              size="small"
              rowKey="parse_id"
              columns={reviewCols}
              dataSource={reviewQueue}
              pagination={{ pageSize: 5 }}
              onRow={(row) => ({
                onClick: () => {
                  setJobId(row.job_id);
                  fetchMatches(row.job_id).then(setMatches).catch(() => setMatches([]));
                },
              })}
            />
            {selectedReview ? (
              <Form
                form={reviewForm}
                layout="vertical"
                onFinish={async (values) => {
                  await reviewJobParse(selectedReview.parse_id, {
                    degree_req: values.degree_req || null,
                    major_codes: csvList(values.major_codes),
                    direction_keywords: csvList(values.direction_keywords),
                    location: values.location || null,
                    needs_review: false,
                    review_reason: null,
                  });
                  setSelectedReview(null);
                  await loadReviewQueue();
                  message.success("复核已保存");
                }}
              >
                <Row gutter={8}>
                  <Col span={6}>
                    <Form.Item name="degree_req" label="学历">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item name="major_codes" label="专业代码">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item name="direction_keywords" label="方向">
                      <Input />
                    </Form.Item>
                  </Col>
                  <Col span={6}>
                    <Form.Item name="location" label="地点">
                      <Input />
                    </Form.Item>
                  </Col>
                </Row>
                <Button type="primary" htmlType="submit">
                  保存复核
                </Button>
              </Form>
            ) : null}
          </Card>
        </Col>
      </Row>

      <Card title="直推名单" style={{ marginTop: 16 }}>
        <Space style={{ marginBottom: 12 }}>
          <InputNumber min={1} value={jobId ?? undefined} onChange={(v) => setJobId(v ?? null)} />
          <Button
            type="primary"
            onClick={async () => {
              if (!jobId) return;
              setMatches(await calculateMatches(jobId));
            }}
          >
            计算
          </Button>
          <Button
            onClick={async () => {
              if (!jobId) return;
              setMatches(await fetchMatches(jobId));
            }}
          >
            刷新
          </Button>
          <Button
            onClick={async () => {
              if (!jobId) return;
              const res = await pushMatches(jobId);
              message.success(`已确认 ${res.pushed} 条`);
              setMatches(await fetchMatches(jobId));
            }}
          >
            确认直推
          </Button>
          <Button
            disabled={!jobId}
            onClick={() => downloadMatches("zh")}
          >
            导出中文 CSV
          </Button>
          <Button disabled={!jobId} onClick={() => downloadMatches("en")}>
            导出英文 CSV
          </Button>
        </Space>
        <Table rowKey="id" size="small" columns={matchCols} dataSource={matches} />
      </Card>
    </div>
  );
}
