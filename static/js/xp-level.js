// ────────────────────────────────
// static/js/xp-level.js
// ────────────────────────────────

import { 
  getFirestore, doc, getDoc, updateDoc 
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { getAuth } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";

const db = getFirestore();
const auth = getAuth();

/**
 * 활동 후 경험치 추가 및 레벨업 체크
 * @param {"call"|"chat"} type - 활동 종류
 */
export async function addXP(type) {
  const user = auth.currentUser;
  if (!user) return;

  const userRef = doc(db, "users", user.uid);
  const snap = await getDoc(userRef);
  if (!snap.exists()) return;

  const userData = snap.data();
  let xp = userData.xp || 0;
  let level = userData.level || 1;

  // ✅ 통화, 채팅 둘 다 10XP 추가
  xp += 10;

  // ✅ 50XP 도달 시 레벨업 
  if (xp >= 50) {
    level += 1;
    xp = xp - 50; // 🔹 넘친 XP는 다음 레벨로 이월되도록 (0으로 리셋해도 상관없음)
    alert(`🎉 레벨 ${level} 달성!`);    // 이건 띄우지말까..
  }

  // ✅ Firestore 업데이트
  await updateDoc(userRef, { xp, level });
  console.log(`✅ XP 업데이트 완료 → level: ${level}, xp: ${xp}`);
}