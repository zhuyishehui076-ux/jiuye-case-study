import { type ReactNode, useEffect } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import { ConfigProvider, Button, Layout, Space, Spin } from "antd";
import { appTheme, dashboardTheme, DASH_BG } from "./theme";
import { Login } from "./pages/Login";
import { Dashboard } from "./pages/Dashboard";
import { Jobs } from "./pages/Jobs";
import { Profiles } from "./pages/Profiles";
import { Assist } from "./pages/Assist";
import { DemoBadge } from "./components/DemoBadge";
import { useAuth } from "./stores/auth";
import { fetchMe } from "./api/client";

function LoginRoute() {
  const nav = useNavigate();
  const token = useAuth((s) => s.token);
  useEffect(() => {
    if (token) nav("/dashboard", { replace: true });
  }, [token, nav]);
  return (
    <ConfigProvider theme={appTheme}>
      <Login />
    </ConfigProvider>
  );
}

function AppRoute({ children }: { children: ReactNode }) {
  const { token, me, logout } = useAuth();
  const nav = useNavigate();
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace />;
  if (!me) return <Spin style={{ display: "block", margin: "120px auto" }} />;
  const staff = me.role === "employment_office" || me.role === "counselor";
  const navItems = [
    { path: "/dashboard", label: "驾驶舱" },
    ...(staff ? [
      { path: "/profiles", label: "学生画像" },
      { path: "/assist", label: "帮扶台账" },
      { path: "/jobs", label: "岗位匹配" },
    ] : []),
  ];
  if (!navItems.some((item) => item.path === location.pathname)) {
    return <Navigate to="/dashboard" replace />;
  }
  return (
    <ConfigProvider theme={dashboardTheme}>
      <Layout style={{ minHeight: "100vh", background: DASH_BG }}>
        <Layout.Header
          style={{
            background: "transparent",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderBottom: "1px solid #1c3a5e",
          }}
        >
          <span style={{ color: "#cfe3f5" }}>
            {me?.display_name ?? me?.username}（{me?.role}）
          </span>
          <Space>
            {navItems.map((item) => {
              const active = location.pathname === item.path;
              return (
                <Button
                  key={item.path}
                  size="small"
                  type={active ? "primary" : "default"}
                  onClick={() => nav(item.path)}
                  style={active ? { fontWeight: 700, boxShadow: "0 0 0 1px #44d5ff" } : undefined}
                >
                  {item.label}
                </Button>
              );
            })}
            <Button size="small" onClick={logout}>
              退出
            </Button>
          </Space>
        </Layout.Header>
        <Layout.Content>
          {children}
        </Layout.Content>
      </Layout>
    </ConfigProvider>
  );
}

export default function App() {
  const { token, me, setMe, logout } = useAuth();

  useEffect(() => {
    if (token && !me) {
      fetchMe().then(setMe).catch(() => logout());
    }
  }, [token, me, setMe, logout]);

  return (
    <BrowserRouter>
      <DemoBadge show={!!me?.show_demo_badge} />
      <Routes>
        <Route path="/login" element={<LoginRoute />} />
        <Route path="/dashboard" element={<AppRoute><Dashboard /></AppRoute>} />
        <Route path="/profiles" element={<AppRoute><Profiles /></AppRoute>} />
        <Route path="/assist" element={<AppRoute><Assist /></AppRoute>} />
        <Route path="/jobs" element={<AppRoute><Jobs /></AppRoute>} />
        <Route path="*" element={<Navigate to={token ? "/dashboard" : "/login"} replace />} />
      </Routes>
    </BrowserRouter>
  );
}
