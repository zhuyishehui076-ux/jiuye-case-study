import { useState } from "react";
import { Button, Card, Form, Input, message } from "antd";
import { login, fetchMe } from "../api/client";
import { useAuth } from "../stores/auth";

// 登录页（非业务页面）。四个演示账号见 README / 种子。
export function Login() {
  const [loading, setLoading] = useState(false);
  const setToken = useAuth((s) => s.setToken);
  const setMe = useAuth((s) => s.setMe);

  const onFinish = async (v: { username: string; password: string }) => {
    setLoading(true);
    try {
      const token = await login(v.username, v.password);
      setToken(token);
      setMe(await fetchMe());
      message.success("登录成功");
    } catch {
      message.error("用户名或密码错误");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: 120 }}>
      <Card title="就业数智系统 · 登录" style={{ width: 360 }}>
        <Form layout="vertical" onFinish={onFinish}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoFocus />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>
            登录
          </Button>
        </Form>
      </Card>
    </div>
  );
}
