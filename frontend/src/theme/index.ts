import { theme as antdTheme } from "antd";
import type { ThemeConfig } from "antd";

// 通用亮色（登录页等）
export const appTheme: ThemeConfig = {
  token: { colorPrimary: "#1677ff", borderRadius: 6 },
};

// 驾驶舱暗色主题（SPEC §6.1：深蓝底 / 青色高亮 / 实体三色红绿灯）
export const dashboardTheme: ThemeConfig = {
  algorithm: antdTheme.darkAlgorithm,
  token: {
    colorPrimary: "#13c2c2", // 青色高亮
    colorBgBase: "#0a1a2f", // 深蓝底
    colorBgContainer: "#0f2440",
    colorBorder: "#1c3a5e",
    borderRadius: 8,
  },
};

// 三色红绿灯（实体胶囊）
export const LIGHT_COLORS: Record<string, string> = {
  red: "#ff4d4f",
  yellow: "#faad14",
  green: "#52c41a",
};
export const CYAN = "#13c2c2";
export const DASH_BG = "#0a1a2f";
