import { create } from "zustand";
import type { Me } from "../api/client";

interface AuthState {
  token: string | null;
  me: Me | null;
  setToken: (t: string | null) => void;
  setMe: (m: Me | null) => void;
  logout: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  token: localStorage.getItem("token"),
  me: null,
  setToken: (t) => {
    if (t) localStorage.setItem("token", t);
    else localStorage.removeItem("token");
    set({ token: t });
  },
  setMe: (m) => set({ me: m }),
  logout: () => {
    localStorage.removeItem("token");
    set({ token: null, me: null });
  },
}));
