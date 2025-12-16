// ────────────────────────────────
//  Firebase SDK Import
// ────────────────────────────────
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import {
  getFirestore, collection, addDoc, serverTimestamp,
  doc, getDoc, updateDoc, getDocs, query, orderBy, limit, setDoc // ✅ setDoc 추가됨
} from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";


// ────────────────────────────────
//  Firebase 설정 (✅ 오타 수정: .appspot.com)
// ────────────────────────────────
const firebaseConfig = {
  apiKey: "AIzaSyBX-GDOIUCtfwXDJXC91z17vj3l4f1fojE",
  authDomain: "onair-project-6df88.firebaseapp.com",
  projectId: "onair-project-6df88",
  storageBucket: "onair-project-6df88.appspot.com",  // ✅ 수정 완료
  messagingSenderId: "269903092073",
  appId: "1:269903092073:web:6fd88d888439182f9a45f8",
  measurementId: "G-6YK7ZS5HD5"
};


// ────────────────────────────────
//  Firebase 초기화
// ────────────────────────────────
const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getFirestore(app);


// ────────────────────────────────
//  전역(window)에 노출 (공용 접근 가능)
// ────────────────────────────────
window.auth = auth;
window.db = db;
window.collection = collection;
window.addDoc = addDoc;
window.serverTimestamp = serverTimestamp;
window.doc = doc;
window.getDoc = getDoc;
window.updateDoc = updateDoc;
window.getDocs = getDocs;
window.query = query;
window.orderBy = orderBy;
window.limit = limit;
window.onAuthStateChanged = onAuthStateChanged;


// ────────────────────────────────
//  로그인 / 로그아웃 상태 처리
// ────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
  const authBtn = document.getElementById("authBtn");
  if (!authBtn) return; // 버튼 없을 경우 종료 (예: 로그인 페이지)

  onAuthStateChanged(auth, async (user) => {
    if (user) {
      // 🔹 로그인 상태
      authBtn.textContent = "로그아웃";
      authBtn.removeAttribute("href");
      authBtn.onclick = async (e) => {
        e.preventDefault();
        try {
          await signOut(auth);
          alert("로그아웃 되었습니다!");
          window.location.href = "/";
        } catch (err) {
          console.error("❌ 로그아웃 실패:", err);
          alert("로그아웃 실패");
        }
      };

      // ✅ 자동 출석 체크 실행
      await autoCheckAttendance(user.uid);

    } else {
      // 🔹 로그아웃 상태
      authBtn.textContent = "로그인";
      authBtn.setAttribute("href", "/login");
      authBtn.onclick = null;
    }
  });
});


// ────────────────────────────────
//  자동 출석 체크 함수
// ────────────────────────────────
async function autoCheckAttendance(uid) {
  try {
    const userRef = doc(db, "users", uid);
    const snap = await getDoc(userRef);
    if (!snap.exists()) return;

    const data = snap.data();
    const att = data.attendance || {};
    const today = new Date().toISOString().split("T")[0];
    const lastCheck = att.last_checkin;

    if (lastCheck !== today) {
      let newStreak = 1;

      // 어제 연속 체크였다면 streak +1
      if (lastCheck) {
        const yesterday = new Date();
        yesterday.setDate(yesterday.getDate() - 1);
        const yyyymmdd = yesterday.toISOString().split("T")[0];
        if (lastCheck === yyyymmdd) {
          newStreak = (att.current_streak || 0) + 1;
        }
      }

      await updateDoc(userRef, {
        "attendance.last_checkin": today,
        "attendance.current_streak": newStreak,
        "attendance.total_days": (att.total_days || 0) + 1,
        "attendance.max_streak": Math.max(newStreak, att.max_streak || 0)
      });

      console.log("✅ 오늘 출석 반영 완료");
    } else {
      console.log("ℹ️ 이미 오늘 출석 기록 있음");
    }
  } catch (error) {
    console.error("❌ 출석 체크 중 오류:", error);
  }
}


// ────────────────────────────────
//  자동 보상 배지 시스템 (✅ 중복 방지 버전)
// ────────────────────────────────
async function checkAndAwardBadges(uid) {
  const userRef = doc(db, "users", uid);
  const badgesRef = collection(userRef, "badges");

  try {
    const callLogs = await getDocs(collection(userRef, "callLogs"));
    const chatLogs = await getDocs(collection(userRef, "chatLogs"));

    // 🏆 첫 대화 달성
    if (callLogs.size + chatLogs.size >= 1) {
      await setDoc(doc(badgesRef, "first_talk"), {  
        id: "first_talk",
        name: "[첫 대화 달성]",
        emoji: "🏆︎",
        earnedAt: serverTimestamp(),
      }, { merge: true }); // 중복 방지
      console.log("✅ 배지 부여: 첫 대화 달성");
    }

    // 🏅 피드백 마스터 (평균 4.5 이상)
    let total = 0, count = 0;
    const allLogs = [...callLogs.docs, ...chatLogs.docs];

    for (const log of allLogs) {
      const data = log.data();
      if (data.feedback && typeof data.feedback.score === "number") {
        total += data.feedback.score;
        count++;
      }
    }

    const avgScore = count > 0 ? total / count : 0;
    if (avgScore >= 4.5) {
      await setDoc(doc(badgesRef, "feedback_master"), {  // ✅ 수정
        id: "feedback_master",
        name: "[피드백 마스터]",
        emoji: "🏅",
        earnedAt: serverTimestamp(),
      }, { merge: true }); // ✅ 중복 방지
      console.log("✅ 배지 부여: 피드백 마스터");
    }

    // ☎️ 통화 10회 달성
    if (callLogs.size >= 10) {
      await setDoc(doc(badgesRef, "call_10"), {  // ✅ 수정
        id: "call_10",
        name: "[통화 10회 달성]",
        emoji: "☎️",
        earnedAt: serverTimestamp(),
      }, { merge: true });
      console.log("✅ 배지 부여: 통화 10회 달성");
    }

    // 💬 채팅 10회 달성
    if (chatLogs.size >= 10) {
      await setDoc(doc(badgesRef, "chat_10"), {  // ✅ 수정
        id: "chat_10",
        name: "[채팅 10회 달성]",
        emoji: "💬",
        earnedAt: serverTimestamp(),
      }, { merge: true });
      console.log("✅ 배지 부여: 채팅 10회 달성");
    }

  } catch (err) {
    console.error("❌ 배지 부여 중 오류:", err);
  }
}
window.checkAndAwardBadges = checkAndAwardBadges;



// ────────────────────────────────
//  Firestore 로그 저장 함수들
// ────────────────────────────────

// (1) 통화 메시지 저장
window.saveCallMessage = async function (sender, text) {
  const user = auth.currentUser;
  if (!user) return console.warn("⚠️ 로그인 필요: 통화 메시지 저장 불가");

  try {
    await addDoc(collection(db, "users", user.uid, "callMessages"), {
      sender,
      text,
      createdAt: serverTimestamp()
    });
    console.log("✅ 통화 메시지 저장 완료");
  } catch (error) {
    console.error("❌ 통화 메시지 저장 실패:", error);
  }
};


// (2) 통화 로그 저장
window.saveCallLog = async function (duration, scenario) {
  const user = auth.currentUser;
  if (!user) return console.warn("⚠️ 로그인 필요: 통화 로그 저장 불가");

  try {
    await addDoc(collection(db, "users", user.uid, "callLogs"), {
      duration,
      scenario,
      createdAt: serverTimestamp()
    });
    console.log("✅ 통화 로그 저장 완료");
  } catch (error) {
    console.error("❌ 통화 로그 저장 실패:", error);
  }
};


// (3) 채팅 로그 저장
window.saveChatLog = async function (message, sender) {
  const user = auth.currentUser;
  if (!user) return console.warn("⚠️ 로그인 필요: 채팅 로그 저장 불가");

  try {
    await addDoc(collection(db, "users", user.uid, "chatLogs"), {
      message,
      sender, // "user" 또는 "ai"
      createdAt: serverTimestamp()
    });
    console.log("✅ 채팅 로그 저장 완료");
  } catch (error) {
    console.error("❌ 채팅 로그 저장 실패:", error);
  }
};