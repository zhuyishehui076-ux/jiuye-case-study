import { Tag } from "antd";

// SPEC §3：demo 档全局角标『演示环境·模拟数据』常显。
// 是否显示由后端 /auth/me 的 show_demo_badge 决定，前端不写死档位。
export function DemoBadge({ show }: { show: boolean }) {
  if (!show) return null;
  return (
    <Tag color="volcano" style={{ position: "fixed", top: 8, right: 8, zIndex: 1000 }}>
      演示环境 · 模拟数据
    </Tag>
  );
}
