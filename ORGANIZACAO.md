# Organizacao das cameras e vagas

## Onde cadastrar cameras

Edite:

```text
config/cameras.json
```

Esse arquivo contem fontes reais de video e deve ficar apenas localmente.
Para publicar o projeto, use `config/cameras.example.json` como modelo.

Cada camera tem:

```json
{
  "id": "cam_002",
  "nome": "Camera 002",
  "rua": "Anchieta Praca Central",
  "cidade": "Anchieta - ES",
  "cep": "29230-000",
  "latitude": -20.805465637271546,
  "longitude": -40.65302960042885,
  "source": "rtsp://host:554/stream1",
  "spots_file": "cameras/cam_002/vagas.json",
  "pixel_threshold": 1000,
  "free_threshold": 500,
  "processing": {
    "adaptive_block": 25,
    "adaptive_c": 16,
    "blur": 5,
    "dilate": 3
  }
}
```

`latitude`/`longitude` sao a posicao real da propria camera (usada so como
referencia; quem aparece no mapa do app sao as vagas, cada uma com seu
proprio lat/lng).

### Fonte do video (`source`)

Aceita:

- Arquivo local: `"carPark.mp4"`
- Webcam do PC: `"0"`
- RTSP/HTTP direto: `"rtsp://host:554/stream1"` ou uma URL
  `.m3u8` (HLS) direta
- Se a camera exigir usuario/senha, coloque essa URL completa apenas no
  `config/cameras.json` local, que fica fora do Git.
- Link do YouTube (live ou video normal): `"https://www.youtube.com/live/ID"`
  — resolvido automaticamente via `yt-dlp` (ver `camera_config.resolve_youtube_stream`)

Links de player embutido em iframe (ex: paginas tipo `cloud.fullcam.me/#/cembed/...`)
**nao funcionam direto** — sao paginas JS que negociam o video por tras via
WebRTC ou HLS. Para usar uma camera dessas, abra a pagina no navegador,
va em DevTools > Network, filtre por `m3u8` e pegue a URL real do stream
(se so aparecer WebSocket/WebRTC, essa camera nao da pra usar aqui).

## Onde ficam as vagas

Cada camera tem sua propria pasta:

```text
cameras/cam_001/vagas.json
cameras/cam_002/vagas.json
```

Cada vaga tem:

```json
{
  "id": "cam_002_vaga_005",
  "nome": "Vaga 005",
  "tipo": "pcd",
  "latitude": -20.805483397361993,
  "longitude": -40.652950475266856,
  "polygon": [[507, 286], [611, 284], [613, 330], [505, 330]]
}
```

- `polygon`: area desenhada na imagem (coordenadas em pixel do frame).
- `latitude`/`longitude`: ponto real da vaga, mostrado no mapa do app.
- `tipo`: `regular` | `pcd` | `idoso` | `carga`. Define o tipo de vaga
  reservada — o app usa isso pra mostrar o icone certo. Vagas normais podem
  omitir o campo (o `calibrate.py` preenche `regular` por padrao).

## Como testar uma camera

```powershell
python test_local.py --camera cam_001
python test_local.py --camera cam_002
```

Como `cam_001` e a camera padrao (`default_camera_id`), tambem pode rodar
`python test_local.py` sem `--camera`.

Vagas com `tipo` diferente de `regular` aparecem com uma tag (`[PCD]`,
`[IDOSO]`) e uma bolinha colorida no canto da vaga.

## Como desenhar ou editar vagas

```powershell
python calibrate.py --camera cam_001
python calibrate.py --camera cam_002
```

Controles: clique adiciona ponto, `N` fecha a vaga atual e comeca a
proxima, `U` desfaz o ultimo ponto, `R` limpa tudo, `S` salva, `Q`/Esc sai.

O `tipo`, `latitude` e `longitude` de vagas ja existentes sao preservados
ao salvar de novo — o `calibrate.py` so mexe no `polygon`. Para marcar o
tipo de uma vaga (pcd/idoso) ou preencher lat/lng, edite o `vagas.json`
diretamente por enquanto (nao tem atalho de teclado pra isso ainda).

## Como adicionar uma nova camera

1. Crie uma nova pasta:

```text
cameras/cam_003/
```

2. Adicione a camera em `config/cameras.json` (com endereco e lat/lng reais
   da camera, se souber).

3. Use um novo arquivo de vagas:

```json
"spots_file": "cameras/cam_003/vagas.json"
```

4. Desenhe as vagas e preencha `tipo`/`latitude`/`longitude` de cada uma:

```powershell
python calibrate.py --camera cam_003
```

## API (servir as vagas pro app mobile)

```powershell
python api_server.py
```

Sobe um servidor Flask (porta 8000 por padrao) que roda a deteccao de
**todas** as cameras cadastradas em `config/cameras.json` em background e
expoe:

- `GET /api/health`
- `POST /api/login` (sem auth real ainda — so estrutura, igual o mock)
- `GET /api/spots?lat=..&lng=..&radius=450`

O app mobile (`vacol/src/api.js`) deve apontar `API_BASE_URL` pro IP da
maquina que roda esse servidor na rede local (nao usar `localhost`).
