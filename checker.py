# check_version.py
import google.generativeai

# 설치된 라이브러리의 실제 버전을 출력합니다.
print(f"실행된 라이브러리 버전: {google.generativeai.__version__}")