import { redirect } from "next/navigation";

/** 첫 화면 = 대시보드 (2026-08-28 지시). 미로그인 시 대시보드가 /login 으로 보낸다. */
export default function Home() {
  redirect("/dashboard");
}
