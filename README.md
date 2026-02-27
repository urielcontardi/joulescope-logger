# Joulescope Logger

App web unificado para captura contínua de dados de consumo de energia via Joulescope, com interface web e visualização em tempo real.

## Funcionalidades

- **Interface web**: Página única com controles de captura e gráficos
- **Captura contínua**: Salva dados em janelas de tempo configuráveis em CSV
- **Visualização em tempo real**: Gráficos de corrente, tensão, potência e energia
- **Docker**: Execução containerizada com acesso USB ao Joulescope

## Estrutura

```
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py              # FastAPI: REST, WebSocket
│       ├── joulescope_manager.py # Captura em background
│       └── static/
│           └── index.html       # Frontend
├── logs/                        # CSVs de experimentos (volume)
├── docker-compose.yml
└── README.md
```

## Uso

### Com Docker (recomendado)

```bash
# Criar diretório de logs no SD (Radxa)
sudo mkdir -p /mnt/external_sd/logs
sudo chown -R $USER:$USER /mnt/external_sd/logs

# Subir o container
docker compose up -d --build

# Acessar
# http://localhost:8080
```

### Sem Docker (desenvolvimento)

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

> **Nota**: O Joulescope precisa estar conectado via USB. No Linux, pode ser necessário adicionar regras udev para acesso ao dispositivo.

## Configuração

| Variável | Default | Descrição |
|----------|---------|-----------|
| `LOG_DIR` | `/app/logs` | Diretório dos arquivos CSV |
| `PORT` | `8080` | Porta HTTP |
| `TZ` | `America/Sao_Paulo` | Fuso horário |

## Localização dos dados

Os arquivos CSV são salvos em `/mnt/external_sd/logs/` (SD externo na Radxa). Crie o diretório antes de subir:

```bash
sudo mkdir -p /mnt/external_sd/logs
sudo chown -R $USER:$USER /mnt/external_sd/logs
```

## Linux: regras udev (obrigatório para USB)

No **host Linux** (antes de rodar o Docker), instale as regras udev para o Joulescope:

```bash
# Opção 1: systemd (usuário logado no console)
sudo ./scripts/install-udev-rules.sh 72

# Opção 2: grupo plugdev (SSH, sem display)
sudo ./scripts/install-udev-rules.sh 99
sudo usermod -a -G plugdev $USER
# Faça logout e login
```

Reconecte o Joulescope após instalar.
