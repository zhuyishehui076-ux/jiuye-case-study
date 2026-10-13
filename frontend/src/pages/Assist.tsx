import { useEffect, useState } from "react";
import { Button, Card, Col, Form, Input, Row, Select, Space, Table, Tag, message } from "antd";
import type { TableColumnsType } from "antd";
import {
  appendAssistRecord,
  exportAssistDocx,
  exportAssistXlsx,
  fetchAssistCases,
  type AssistCaseRow,
} from "../api/client";
import { CYAN } from "../theme";

const ACTIONS = ["约谈", "推岗", "培训", "走访", "其他"].map((value) => ({
  label: value,
  value,
}));

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function Assist() {
  const [rows, setRows] = useState<AssistCaseRow[]>([]);
  const [selected, setSelected] = useState<AssistCaseRow | null>(null);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();

  const load = () => fetchAssistCases().then((data) => {
    setRows(data);
    if (!selected && data.length > 0) setSelected(data[0]);
  });

  useEffect(() => {
    load();
  }, []);

  const columns: TableColumnsType<AssistCaseRow> = [
    { title: "学号", dataIndex: "student_no", key: "student_no", width: 120 },
    { title: "姓名", dataIndex: "student_name", key: "student_name", width: 90 },
    { title: "学院", dataIndex: "college_name", key: "college_name" },
    { title: "状态", dataIndex: "status", key: "status", width: 100 },
    {
      title: "记录",
      dataIndex: "record_count",
      key: "record_count",
      width: 80,
      render: (v: number) => <Tag color={v > 0 ? "green" : "gold"}>{v}</Tag>,
    },
    { title: "建档原因", dataIndex: "opened_reason", key: "opened_reason" },
  ];

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 20 }}>
        <h1 style={{ color: CYAN, margin: 0 }}>一生一策帮扶台账</h1>
        <Space>
          <Button
            onClick={async () => download(await exportAssistXlsx(), "assist-ledger.xlsx")}
          >
            导出 xlsx
          </Button>
          <Button
            onClick={async () => download(await exportAssistDocx(), "assist-ledger.docx")}
          >
            导出 docx
          </Button>
        </Space>
      </Space>

      <Row gutter={16}>
        <Col span={15}>
          <Card title={`案例列表（全部 ${rows.length}）`}>
            <Table
              size="small"
              rowKey="id"
              columns={columns}
              dataSource={rows}
              pagination={{ pageSize: 10 }}
              expandable={{
                expandedRowRender: (row) => (
                  <div style={{ color: "#d8eefb", whiteSpace: "pre-wrap" }}>
                    {row.action_summary || "暂无记录"}
                  </div>
                ),
                rowExpandable: () => true,
              }}
              rowClassName={(row) => row.id === selected?.id ? "ant-table-row-selected" : ""}
              onRow={(row) => ({ onClick: () => setSelected(row) })}
            />
          </Card>
        </Col>
        <Col span={9}>
          <Card title="追加帮扶记录">
            {selected ? (
              <Space direction="vertical" style={{ width: "100%" }} size={14}>
                <div>
                  <div style={{ color: "#d8eefb", fontWeight: 700 }}>
                    {selected.student_name} · {selected.student_no}
                  </div>
                  <div style={{ color: "#9fc7e8" }}>{selected.college_name}</div>
                  <div style={{ color: "#9fc7e8", marginTop: 8 }}>
                    {selected.risk_reason || selected.opened_reason}
                  </div>
                </div>
                <Form
                  form={form}
                  layout="vertical"
                  onFinish={async (values) => {
                    setLoading(true);
                    try {
                      await appendAssistRecord(selected.id, {
                        action_type: values.action_type,
                        note: values.note,
                      });
                      form.resetFields();
                      await load();
                      message.success("记录已追加");
                    } finally {
                      setLoading(false);
                    }
                  }}
                >
                  <Form.Item
                    name="action_type"
                    label="动作类型"
                    rules={[{ required: true }]}
                    initialValue="约谈"
                  >
                    <Select options={ACTIONS} />
                  </Form.Item>
                  <Form.Item name="note" label="备注" rules={[{ required: true }]}>
                    <Input.TextArea rows={7} />
                  </Form.Item>
                  <Button type="primary" htmlType="submit" loading={loading}>
                    追加记录
                  </Button>
                </Form>
                <Card size="small" title="动作汇总">
                  <div style={{ color: "#d8eefb", whiteSpace: "pre-wrap" }}>
                    {selected.action_summary || "暂无记录"}
                  </div>
                </Card>
              </Space>
            ) : (
              <div style={{ color: "#9fc7e8" }}>暂无案例</div>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
