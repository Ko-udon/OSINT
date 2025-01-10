import re
import git
import pygit2
import json
import git.repo
import pytz
import PyPDF2
import urllib3
import requests
import structlog


from pathlib import Path
from datetime import datetime, timedelta
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from apscheduler.schedulers.background import BackgroundScheduler

# model
from .models import SendUser

# mail
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib

# 경고 무시
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
LOGGER = structlog.getLogger(__name__)
today_date = timezone.now().astimezone(pytz.timezone('Asia/Seoul')).strftime('%Y-%m-%d')
yesterday_date = (timezone.now().astimezone(pytz.timezone('Asia/Seoul')) - timedelta(days=1)).strftime('%Y-%m-%d')
korea_timezone = pytz.timezone('Asia/Seoul')            
end_time_utc_formatted = timezone.now() - timedelta(seconds=10) 
start_time_utc_formatted = (timezone.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

scheduler = BackgroundScheduler()

# 중복 처리를 위한 배열
duplicate_ioc = []
duplicate_sha = []

class request(APIView):
    def post(self, request):
        result_list = []
        try:
            since = request.GET.get('since')
            until = request.GET.get('until')
            utc_tz = pytz.utc
            
            if since is None or until is None:
                return Response("since와 until 값을 모두 제공해야 합니다.")

            since = datetime.strptime(since, '%Y-%m-%d').replace(
            hour=19, minute=0, second=0, microsecond=0, tzinfo=utc_tz)

            today = timezone.now().date()
            until_date = timezone.datetime.strptime(until, '%Y-%m-%d').date()
            
            if until_date == today:
                until = datetime.now(utc_tz) - timedelta(seconds=30)
            else:
                until = datetime.combine(until_date, datetime.min.time()).replace(
                    hour=23, minute=59, second=59, microsecond=999999, tzinfo=utc_tz)
        except ValueError:
            return Response("since와 until 값은 'YYYY-MM-DD' 형식이어야 합니다.")
        
        repos_path = Path('./github/repo')
        
        for repo_path in repos_path.iterdir():
            if repo_path.is_dir():
                origin = git.Repo(repo_path).remotes.origin
                origin.pull()
                
                repo = pygit2.Repository(repo_path)
                commits = []
                for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_NONE):
                    commit_time = datetime.fromtimestamp(commit.commit_time, utc_tz)
                    if since <= commit_time <= until:
                        commits.append(commit)
                        
                if commits:
                    for commit in commits:
                        parent_commit = commit.parents[0] if commit.parents else None
                        diff = repo.diff(parent_commit, commit) if parent_commit else repo.diff(commit)
                        for patch in diff:
                            file_path = patch.delta.new_file.path
                            LOGGER.debug(f'파일: {file_path}')
                            
                            # PDF 파일인 경우
                            if file_path.endswith('.pdf'):
                                # LOGGER.debug(f"커밋 중 {file_path}는 PDF 파일입니다.")
                                
                                # file_path = Path(repo.path).parent / patch.delta.old_file.path
                                # LOGGER.debug(f"pdf 파일 경로: {file_path}")
                                # with open(file_path, 'rb') as pdf_file:
                                #     pdf_reader = PyPDF2.PdfReader(pdf_file)
                                #     full_text = "".join(page.extract_text() for page in pdf_reader.pages if page.extract_text()) 
                                pass
                            else:
                                added_lines = [line for line in patch.text.splitlines() if line.startswith('+') and not line.startswith('+++')]
                                
                                full_text = ''
                                for line in added_lines:
                                    full_text += line[1:] + '\n' 
                                    
                            if repo.remotes:
                                remote_url = repo.remotes[0].url  
                                owner_repo = remote_url.split('/')[-2:]  
                                owner = owner_repo[0]  
                            
                            commit_sha = str(commit.id)
                            repo_name = Path(repo_path).name
                            link = f"https://github.com/{owner}/{repo_name}/commit/{commit_sha}"
                            commit_title = commit.message.split('\n')[0]
                            
                            if commit_sha not in duplicate_sha:
                                result_list.append({
                                        "title": commit_title,
                                        "url": link,
                                        "text": full_text
                                    }
                                )
                                duplicate_sha.append(commit_sha)
                                
        response_data = {
            'message': 'Github crawling success',
            'data': result_list
        }
        # email 전송
        try:
            mail_sender(response_data)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
        return Response(response_data, status=200)

def mail_sender(response_data):
    today = datetime.now()
    today_str = today.strftime('%Y-%m-%d %H:%M')
    
    try:
        data = response_data
        if not isinstance(data, dict):
            return JsonResponse({'error': 'JSON 형식이 올바르지 않습니다.'}, status=400)
    except (AttributeError, TypeError, ValueError) as e:
        return JsonResponse({'error': f'요청 데이터를 처리하는 중 오류가 발생했습니다: {str(e)}'}, status=400)
    # 필드 검증
    required_fields = ['message', 'data']
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return JsonResponse({
            'error': f'필수 필드가 누락되었습니다: {", ".join(missing_fields)}'
        }, status=400)
    # 이메일 본문
    body = f"""
================================================\n

"""
    for commit in data['data']:
            body += f"URL: {commit['url']}\n제목: {commit['title']}\n내용:\n{commit['text']}\n================================================\n"
        
            
    email = settings.__getattr__('EMAIL')
    email_password = settings.__getattr__('EMAIL_PASSWORD')
    
    
    msg = MIMEMultipart()
    subject = f"Github Repo Commit 리포트 - ({today_str})"
    msg['Subject'] = subject
    msg['From'] = email
    msg.attach(MIMEText(body, 'plain'))
    recipients = list(SendUser.objects.values_list('email', flat=True))
    # msg['To'] = ', '.join(recipients)
    msg['Bcc'] = ', '.join(recipients)

    try:
        with smtplib.SMTP("smtp.office365.com", 587) as server:
            server.starttls()
            server.login(email, email_password)
            server.send_message(msg)
            return Response('메일을 성공적으로 전송하였습니다!', status=status.HTTP_200_OK)
    except Exception as e:
        LOGGER.error(f"메일 전송 실패: {e}")
        return JsonResponse({'error': str(e)}, status=500)
        

def github_ioc_scheduler():
        LOGGER.error("스케줄러 시작")
        url = "http://127.0.0.1:8000/github/request/" # cron
        query_params = { 
                'since': yesterday_date,
                'until': today_date
        }
        try:
            response = requests.post(url, params= query_params)  
            if response.status_code == 200:
                data = response.json()
                return JsonResponse(data)
            else:
                return JsonResponse({'error': 'Github IOC API 요청 실패'}, status=response.status_code)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

def initialize_duplicate_ioc():
    global duplicate_ioc
    duplicate_ioc = []

    global duplicate_sha
    duplicate_sha = []
    LOGGER.debug("Github 중복 검사 배열 초기화 완료") 
    
scheduler.add_job(github_ioc_scheduler, 'cron', hour='22', minute='30') # 매일 8~19시 정각 마다 // 9시, 13시, 19시로 변경
scheduler.add_job(initialize_duplicate_ioc, 'cron', hour=0, minute=0) # 00시 중복 체크 배열 초기화