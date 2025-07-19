@echo off
cd /d %~dp0
git init
git add .
git commit -m "feat: 机器人定时自动发送消息"
git remote add origin https://github.com/xiaohui005/jiqiren.git
git push -u origin master
pause