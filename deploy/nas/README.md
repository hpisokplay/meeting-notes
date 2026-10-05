# DD會議紀錄 NAS 部署

## 資料位置
- 程式（前端＋後端，掛載）：`/volume1/docker/meetings/app`（`server/` + `web/`）
- 資料主本：`/volume1/projects/meetings`（容器內 `/data`，Telegram 機器人看到 `/projects/meetings`）
- 備份：`/volume1/docker/backups/meetings`
- 轉錄器金鑰：`/volume1/docker/meetings/worker.key`（填到電腦轉錄器設定）
- 網址：https://ddys.tail95e8ec.ts.net:8443/

## 第一次安裝（只做一次）
前置（由 Claude 用 ssh 完成）：把 `deploy/nas/` 內的檔案放到 NAS `/volume1/docker/meetings/`，並先跑一次 `update_app.sh` 讓 `app/` 有程式。

然後你只要在 NAS 的 ssh 輸入這一行（會問 sudo 密碼）：

```
sudo sh /volume1/docker/meetings/install.sh
```

它會建資料夾、產生 `worker.key`、建置並啟動容器、設定 `tailscale serve`（8443），最後印出狀態。

## 更新程式
在專案根目錄（Git Bash）執行：

```
sh deploy/nas/update_app.sh
```

它把 `server/` 與前端檔案用 tar over ssh 傳到 NAS、舊的 `app` 改名成 `app.old-時間`（不刪除）、新增 `.restart` 讓容器自己重啟。不需要 sudo、不需要重建映像。
只有改 `Dockerfile` / `requirements.txt` 時才需要重新 `sudo ... docker compose up -d --build`。

## 看紀錄
```
sudo /usr/local/bin/docker logs --tail 100 meetings
```
