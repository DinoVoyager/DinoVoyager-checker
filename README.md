# DinoVoyager-checker 🦖🔍

[English](#english) | [Русский](#русский)

---

<a name="english"></a>
## English

**DinoVoyager-checker** is a lightweight, distributed connectivity monitoring agent packaged as a secure, minimal Docker container (<100MB) designed to run on users' home servers, Raspberry Pis, NAS devices, or VPS instances.

It connects to your DinoVoyager proxy subscription, continuously tests real round-trip connectivity across all routes using an embedded `xray-checker` engine, binds to your configured region (e.g. RU-MOW), and reports health telemetry back to the DinoVoyager Admin Panel without external geo lookups or coordinates.

### Features
- 🚀 **Zero External Dependencies**: Pure Python 3 standard library runtime with embedded Go binary.
- 🛡️ **Hardened Container**: Runs on Alpine 3.21 under an unprivileged non-root user (`appuser`, UID 1000).
- ⏱️ **Real Latency & Health Checks**: Measures real TCP/HTTP round-trip times and availability percentage per route.
- 🌍 **Regional Telemetry**: Simple regional binding (e.g. `RU-MOW`) directly mapped to the regional tile map. No third-party geo tracking and no coordinates required.
- 🔄 **Autonomous Supervisor**: Self-healing supervisor daemon with automatic crash recovery, async pipe draining (deadlock-free), and graceful signal handling (`SIGTERM`/`SIGINT`).
- 📊 **Central Telemetry**: Securely pushes reports to DinoVoyager Admin API (`POST /api/checkers/report`) using `X-API-Key` authentication.

---

### Quick Start

#### 1. One-Line Installation via curl (Recommended)

You can install, configure, and start the checker with a single terminal command:

**Interactive mode** (prompts for API key, subscription URL, and name):
```bash
curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash
```

**Non-interactive mode** (pass all parameters directly):
```bash
curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash -s -- \
  --api-key "dvc_live_a1b2c3d4e5f67890..." \
  --subscription-url "https://app.dinovoyager.net/api/sub/my-sub-token" \
  --api-url "https://app.dinovoyager.net" \
  --name "Home-London-Server" \
  --interval 300 \
  -y
```

The script will automatically:
1. Verify (or optionally install) Docker.
2. Create an isolated installation folder (`/opt/dinovoyager-checker` or `~/dinovoyager-checker`).
3. Securely store your credentials in `.env` (`chmod 600`).
4. Pull `as1aw/checker:latest` and launch the container with auto-restart (`--restart unless-stopped`).
5. Generate a local `update.sh` for 1-click future updates.

---

#### 2. Manual Start with Docker Run

```bash
docker run -d \
  --name dinovoyager-checker \
  --restart unless-stopped \
  -e SUBSCRIPTION_URL="https://app.dinovoyager.net/api/sub/my-sub-token" \
  -e POLLING_INTERVAL=300 \
  -e API_URL="https://app.dinovoyager.net" \
  -e API_KEY="dvc_live_a1b2c3d4e5f67890..." \
  -e CHECKER_NAME="Home-London-Server" \
  as1aw/checker:latest
```

#### 3. Manual Start with Docker Compose

Save as `docker-compose.yml`:

```yaml
version: "3.8"

services:
  dinovoyager-checker:
    image: as1aw/checker:latest
    container_name: dinovoyager-checker
    restart: unless-stopped
    environment:
      - SUBSCRIPTION_URL=https://app.dinovoyager.net/api/sub/my-sub-token
      - POLLING_INTERVAL=300
      - API_URL=https://app.dinovoyager.net
      - API_KEY=dvc_live_a1b2c3d4e5f67890...
      - CHECKER_NAME=Home-RaspberryPi
      - LOG_LEVEL=INFO
      - PROXY_TIMEOUT=10
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

Start the service:
```bash
docker compose up -d
```

---

### Configuration Parameters

| Parameter | CLI Flag | Environment Variable | Required | Default | Description |
|---|---|---|---|---|---|
| Subscription URL | `-s`, `--subscription-url` | `SUBSCRIPTION_URL` | **Yes** | — | VLESS/VMess/Trojan subscription URL delivering routes |
| Polling Interval | `-i`, `--interval` | `POLLING_INTERVAL` | **Yes** | `300` | Testing cadence in seconds (default: 5 minutes, minimum `15` seconds) |
| API URL | `-u`, `--api-url` | `API_URL` | **Yes** | `https://app.dinovoyager.net` | Central API endpoint (trailing slash stripped) |
| API Key | `-k`, `--api-key` | `API_KEY` | **Yes** | — | Authentication API key generated in Admin Panel |
| Checker Name | `-n`, `--name` | `CHECKER_NAME` | No | Hostname | Human-readable checker identifier |
| Region Code | `-r`, `--region` | `REGION_CODE` | No | `RU-MOW` | Assigned Russian region code for regional tile map |
| Log Level | `--log-level` | `LOG_LEVEL` | No | `INFO` | Verbosity (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| Proxy Timeout | `--timeout` | `PROXY_TIMEOUT` | No | `10` | Per-route connectivity probe timeout in seconds |

> **Note**: Command-line arguments strictly take precedence over environment variables.

---

### Updating the Checker

To update an existing installation to the latest version while preserving all your credentials and configuration:

#### Linux / macOS / Raspberry Pi
```bash
cd /opt/dinovoyager-checker   # or ~/dinovoyager-checker
./update.sh
```

#### Windows (PowerShell)
```powershell
.\update.ps1
```

Or manually via Docker Compose:
```bash
docker compose pull
docker compose up -d --remove-orphans
```

---

### Troubleshooting & Diagnostics

- **View Live Logs**:
  ```bash
  docker logs -f dinovoyager-checker
  ```
- **Inspect Latest Report Payload**:
  ```bash
  docker exec -it dinovoyager-checker cat /tmp/dinovoyager-checker-latest.json
  ```
- **Security Warning on 401/403**:
  If the API key is revoked or mistyped, the checker logs a high-visibility warning with masked key (`dvc_live_****...`) and halts report retries to prevent spam. Re-generate a valid key in the DinoVoyager Admin Panel.

---

<a name="русский"></a>
## Русский

**DinoVoyager-checker** — это легковесный распределенный агент мониторинга сетевой связности, упакованный в безопасный минималистичный Docker-контейнер (<100 МБ), предназначенный для запуска на домашних серверах пользователей, Raspberry Pi, NAS или VPS.

Агент подключается к подписке DinoVoyager, непрерывно проверяет реальную доступность и пинг всех маршрутов через встроенный движок `xray-checker`, привязывается к назначенному региону (например, RU-MOW) и передает телеметрию в панель администратора DinoVoyager без внешних запросов к сервисам геолокации и без координат.

### Возможности
- 🚀 **Без внешних зависимостей**: Написан на стандартной библиотеке Python 3 со встроенным скомпилированным Go-бинарником.
- 🛡️ **Безопасность**: Базовый образ Alpine 3.21, запуск от непривилегированного пользователя `appuser` (UID 1000).
- ⏱️ **Реальные тесты**: Проверка TCP/HTTP связности, замер пинга в миллисекундах и расчет процента надежности (reliability %).
- 🌍 **Региональный мониторинг**: Передача телеметрии с привязкой к региону (например, `RU-MOW`) для отображения на региональной карте доступности. Без внешних запросов к сервисам геолокации и без координат.
- 🔄 **Автономный супервизор**: Фоновый процесс с автовосстановлением при сбоях, асинхронным чтением логов (защита от дедлоков пайпов) и корректной обработкой сигналов (`SIGTERM`/`SIGINT`).
- 📊 **Интеграция с DinoVoyager**: Передача отчетов на `POST /api/checkers/report` с авторизацией по ключу `X-API-Key`.

---

### Быстрый старт

#### 1. Установка одной командой через curl (Рекомендуется)

Вы можете настроить и запустить чекер одной строкой в терминале Linux / macOS / Raspberry Pi:

**Интерактивная установка** (скрипт спросит API-ключ, подписку и имя):
```bash
curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash
```

**Автоматическая установка без диалогов** (передача всех параметров напрямую):
```bash
curl -fsSL https://raw.githubusercontent.com/DinoVoyager/DinoVoyager-checker/main/install.sh | bash -s -- \
  --api-key "dvc_live_a1b2c3d4e5f67890..." \
  --subscription-url "https://app.dinovoyager.net/api/sub/my-sub-token" \
  --api-url "https://app.dinovoyager.net" \
  --name "Home-Moscow-Server" \
  --interval 300 \
  -y
```

Что делает скрипт установки:
1. Проверяет наличие Docker (при необходимости предлагает установить его в один клик).
2. Создает изолированную директорию (`/opt/dinovoyager-checker` или `~/dinovoyager-checker`).
3. Надежно сохраняет конфигурацию в `.env` с правами доступа `600`.
4. Скачивает свежий образ `as1aw/checker:latest` и запускает контейнер с автоперезапуском (`--restart unless-stopped`).
5. Создает локальный скрипт `update.sh` для быстрого обновления одной командой в будущем.

---

#### 2. Ручной запуск через Docker CLI

```bash
docker run -d \
  --name dinovoyager-checker \
  --restart unless-stopped \
  -e SUBSCRIPTION_URL="https://app.dinovoyager.net/api/sub/my-sub-token" \
  -e POLLING_INTERVAL=300 \
  -e API_URL="https://app.dinovoyager.net" \
  -e API_KEY="dvc_live_a1b2c3d4e5f67890..." \
  -e CHECKER_NAME="Home-Moscow-Server" \
  as1aw/checker:latest
```

#### 3. Ручной запуск через Docker Compose

Создайте файл `docker-compose.yml`:

```yaml
version: "3.8"

services:
  dinovoyager-checker:
    image: as1aw/checker:latest
    container_name: dinovoyager-checker
    restart: unless-stopped
    environment:
      - SUBSCRIPTION_URL=https://app.dinovoyager.net/api/sub/my-sub-token
      - POLLING_INTERVAL=300
      - API_URL=https://app.dinovoyager.net
      - API_KEY=dvc_live_a1b2c3d4e5f67890...
      - CHECKER_NAME=Home-RaspberryPi
      - LOG_LEVEL=INFO
      - PROXY_TIMEOUT=10
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

Запустите сервис:
```bash
docker compose up -d
```

---

### Таблица параметров

| Параметр | Флаг CLI | Переменная ENV | Обязателен | По умолчанию | Описание |
|---|---|---|---|---|---|
| Ссылка на подписку | `-s`, `--subscription-url` | `SUBSCRIPTION_URL` | **Да** | — | URL подписки с маршрутами VLESS/VMess/Trojan |
| Интервал опроса | `-i`, `--interval` | `POLLING_INTERVAL` | **Да** | `300` | Интервал между проверками в секундах (по умолчанию 5 минут, минимум `15`) |
| URL API DinoVoyager | `-u`, `--api-url` | `API_URL` | **Да** | `https://app.dinovoyager.net` | Адрес сервера DinoVoyager |
| Ключ API чекера | `-k`, `--api-key` | `API_KEY` | **Да** | — | Ключ доступа, созданный в админ-панели |
| Имя чекера | `-n`, `--name` | `CHECKER_NAME` | Нет | Имя хоста | Понятное имя узла мониторинга |
| Код региона | `-r`, `--region` | `REGION_CODE` | Нет | `RU-MOW` | Код региона РФ для отображения на плитке доступности |
| Уровень логов | `--log-level` | `LOG_LEVEL` | Нет | `INFO` | `DEBUG`, `INFO`, `WARN`, `ERROR` |
| Таймаут прокси | `--timeout` | `PROXY_TIMEOUT` | Нет | `10` | Таймаут проверки одного прокси в секундах |

---

### Обновление чекера

Чтобы обновить чекер до последней версии с сохранением всех настроек и переменных окружения:

#### Linux / macOS / Raspberry Pi
```bash
cd /opt/dinovoyager-checker   # или ~/dinovoyager-checker
./update.sh
```

#### Windows (PowerShell)
```powershell
.\update.ps1
```

Либо вручную через Docker Compose:
```bash
docker compose pull
docker compose up -d --remove-orphans
```

---

### Диагностика и устранение неполадок

- **Просмотр логов**:
  ```bash
  docker logs -f dinovoyager-checker
  ```
- **Просмотр последнего отправленного отчета**:
  ```bash
  docker exec -it dinovoyager-checker cat /tmp/dinovoyager-checker-latest.json
  ```
- **Ошибка 401/403 (Invalid API Key)**:
  Если ключ аннулирован в админ-панели или введен с ошибкой, чекер прекратит повторные попытки отправки и выведет предупреждение. Сгенерируйте новый ключ в панели управления DinoVoyager.
