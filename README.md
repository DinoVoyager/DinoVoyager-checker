# DinoVoyager-checker 🦖🔍

[English](#english) | [Русский](#русский)

---

<a name="english"></a>
## English

**DinoVoyager-checker** is a lightweight, distributed connectivity monitoring agent packaged as a secure, minimal Docker container (<100MB) designed to run on users' home servers, Raspberry Pis, NAS devices, or VPS instances.

It connects to your DinoVoyager proxy subscription, continuously tests real round-trip connectivity across all routes using an embedded `xray-checker` engine, detects origin residential ISP and geographic coordinates, and reports health telemetry back to the DinoVoyager Admin Panel.

### Features
- 🚀 **Zero External Dependencies**: Pure Python 3 standard library runtime with embedded Go binary.
- 🛡️ **Hardened Container**: Runs on Alpine 3.21 under an unprivileged non-root user (`appuser`, UID 1000).
- ⏱️ **Real Latency & Health Checks**: Measures real TCP/HTTP round-trip times and availability percentage per route.
- 🌍 **Origin ISP & Geo Intelligence**: Multi-provider origin ISP, ASN, and coordinate detection with smart 4-hour in-memory TTL caching.
- 🔄 **Autonomous Supervisor**: Self-healing supervisor daemon with automatic crash recovery, async pipe draining (deadlock-free), and graceful signal handling (`SIGTERM`/`SIGINT`).
- 📊 **Central Telemetry**: Securely pushes reports to DinoVoyager Admin API (`POST /api/checkers/report`) using `X-API-Key` authentication.

---

### Quick Start

#### 1. Quick Start with Docker Run (Environment Variables)

```bash
docker run -d \
  --name dinovoyager-checker \
  --restart unless-stopped \
  -e SUBSCRIPTION_URL="https://app.dinovoyager.net/api/sub/my-sub-token" \
  -e POLLING_INTERVAL=60 \
  -e API_URL="https://app.dinovoyager.net" \
  -e API_KEY="dvc_live_a1b2c3d4e5f67890..." \
  -e CHECKER_NAME="Home-London-Server" \
  dinovoyager-checker:latest
```

#### 2. Quick Start with Docker Run (CLI Flags)

```bash
docker run -d \
  --name dinovoyager-checker \
  --restart unless-stopped \
  dinovoyager-checker:latest \
  --subscription-url "https://app.dinovoyager.net/api/sub/my-sub-token" \
  --interval 60 \
  --api-url "https://app.dinovoyager.net" \
  --api-key "dvc_live_a1b2c3d4e5f67890..." \
  --name "Home-London-Server"
```

#### 3. Quick Start with Docker Compose

Save as `docker-compose.yml`:

```yaml
version: "3.8"

services:
  dinovoyager-checker:
    image: dinovoyager-checker:latest
    container_name: dinovoyager-checker
    restart: unless-stopped
    environment:
      - SUBSCRIPTION_URL=https://app.dinovoyager.net/api/sub/my-sub-token
      - POLLING_INTERVAL=60
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
| Polling Interval | `-i`, `--interval` | `POLLING_INTERVAL` | **Yes** | `60` | Testing cadence in seconds (minimum `15` seconds) |
| API URL | `-u`, `--api-url` | `API_URL` | **Yes** | — | DinoVoyager central API endpoint (trailing slash stripped) |
| API Key | `-k`, `--api-key` | `API_KEY` | **Yes** | — | Authentication API key generated in Admin Panel |
| Checker Name | `-n`, `--name` | `CHECKER_NAME` | No | Hostname | Human-readable checker identifier |
| Log Level | `--log-level` | `LOG_LEVEL` | No | `INFO` | Verbosity (`DEBUG`, `INFO`, `WARN`, `ERROR`) |
| Proxy Timeout | `--timeout` | `PROXY_TIMEOUT` | No | `10` | Per-route connectivity probe timeout in seconds |

> **Note**: Command-line arguments strictly take precedence over environment variables.

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

Агент подключается к подписке DinoVoyager, непрерывно проверяет реальную доступность и пинг всех маршрутов через встроенный движок `xray-checker`, определяет домашнего интернет-провайдера (ISP), ASN и географические координаты, и передает телеметрию в панель администратора DinoVoyager.

### Возможности
- 🚀 **Без внешних зависимостей**: Написан на стандартной библиотеке Python 3 со встроенным скомпилированным Go-бинарником.
- 🛡️ **Безопасность**: Базовый образ Alpine 3.21, запуск от непривилегированного пользователя `appuser` (UID 1000).
- ⏱️ **Реальные тесты**: Проверка TCP/HTTP связности, замер пинга в миллисекундах и расчет процента надежности (reliability %).
- 🌍 **Geo & ISP Intelligence**: Автоматическое определение провайдера, ASN и координат с умным кешированием на 4 часа.
- 🔄 **Автономный супервизор**: Фоновый процесс с автовосстановлением при сбоях, асинхронным чтением логов (защита от дедлоков пайпов) и корректной обработкой сигналов (`SIGTERM`/`SIGINT`).
- 📊 **Интеграция с DinoVoyager**: Передача отчетов на `POST /api/checkers/report` с авторизацией по ключу `X-API-Key`.

---

### Быстрый старт

#### 1. Запуск через Docker CLI (Переменные окружения)

```bash
docker run -d \
  --name dinovoyager-checker \
  --restart unless-stopped \
  -e SUBSCRIPTION_URL="https://app.dinovoyager.net/api/sub/my-sub-token" \
  -e POLLING_INTERVAL=60 \
  -e API_URL="https://app.dinovoyager.net" \
  -e API_KEY="dvc_live_a1b2c3d4e5f67890..." \
  -e CHECKER_NAME="Home-Moscow-Server" \
  dinovoyager-checker:latest
```

#### 2. Запуск через Docker Compose

Создайте файл `docker-compose.yml`:

```yaml
version: "3.8"

services:
  dinovoyager-checker:
    image: dinovoyager-checker:latest
    container_name: dinovoyager-checker
    restart: unless-stopped
    environment:
      - SUBSCRIPTION_URL=https://app.dinovoyager.net/api/sub/my-sub-token
      - POLLING_INTERVAL=60
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
| Интервал опроса | `-i`, `--interval` | `POLLING_INTERVAL` | **Да** | `60` | Интервал между проверками в секундах (минимум `15`) |
| URL API DinoVoyager | `-u`, `--api-url` | `API_URL` | **Да** | — | Адрес сервера DinoVoyager |
| Ключ API чекера | `-k`, `--api-key` | `API_KEY` | **Да** | — | Ключ доступа, созданный в админ-панели |
| Имя чекера | `-n`, `--name` | `CHECKER_NAME` | Нет | Имя хоста | Понятное имя узла мониторинга |
| Уровень логов | `--log-level` | `LOG_LEVEL` | Нет | `INFO` | `DEBUG`, `INFO`, `WARN`, `ERROR` |
| Таймаут прокси | `--timeout` | `PROXY_TIMEOUT` | Нет | `10` | Таймаут проверки одного прокси в секундах |

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
