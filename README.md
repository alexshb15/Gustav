# Gustav

Copiloto de IA por voz para o **IL-2 Sturmovik: Great Battles**. Você fala com ele enquanto voa; ele acompanha a telemetria do avião em tempo real e responde, lê checklists e avisa de problemas.

> **Estado do projeto:** início do desenvolvimento. A captura de telemetria (Fase 0) está implementada; voz, conversa e alertas ainda não. O plano completo está em [PLANO.md](PLANO.md).

## O que ele vai fazer

- **Responder por voz** a perguntas sobre o avião e o voo: "como está o motor?", "qual a potência de subida?", "qual tecla trava a bequilha?".
- **Avisar sem ser perguntado**: trem recolhido na aproximação, estol iminente, tempo estourando em potência de emergência, flaps acima da velocidade limite.
- **Guiar checklists** de decolagem, subida, combate e pouso, avançando sozinho quando a telemetria confirma cada passo.
- **Só aconselhar.** Ele nunca aperta teclas nem envia comandos ao jogo.

O foco inicial é a família **Bf 109**, jogando em **VR**, com conversa em **português do Brasil**.

## Como funciona

```
 Microfone ──► Push-to-talk ──► Transcrição (Whisper)
                                       │
 IL-2 ──UDP──► Telemetria ──► Estado do voo ──► Claude + ferramentas ──► Voz (Piper) ──► Fone
                                       │                 ▲
                                       ▼                 │
                               Motor de alertas ─────────┘
                               (regras, sem LLM)
```

- **Telemetria:** o jogo envia dados por UDP (velocidade, altura, RPM, pressão de admissão, trem, flaps, carga G, atitude).
- **Alertas:** regras fixas, fora do modelo de linguagem, para o aviso sair em menos de meio segundo.
- **Conversa:** push-to-talk em um botão do HOTAS, transcrição local, resposta do Claude com acesso ao estado do voo e à base de dados do avião, fala sintetizada no headset.

## Usando o que já existe

O [tools/sniff_telemetry.py](tools/sniff_telemetry.py) escuta a telemetria do jogo, mostra os valores ao vivo e grava o voo. Só usa a biblioteca padrão do Python.

1. Com o jogo fechado, faça uma cópia de `data/startup.cfg` e acrescente ao arquivo:

   ```
   [KEY = telemetrydevice]
       addr = "127.0.0.1"
       decimation = 2
       enable = true
       port = 4322
   [END]

   [KEY = motiondevice]
       addr = "127.0.0.1"
       decimation = 2
       enable = true
       port = 4321
   [END]
   ```

2. Rode o script e entre em um voo:

   ```
   python tools/sniff_telemetry.py
   ```

   A telemetria só sai com você dentro do avião. A cada meio segundo aparece uma linha com velocidade, altura, RPM, pressão de admissão, trem, flaps e ângulo de ataque.

3. `Ctrl+C` encerra e mostra um resumo dos campos recebidos, faixas de valores e frequência. A gravação e o resumo ficam em `captures/`.

Opções: `--no-record` só mostra, sem gravar; `--raw` despeja cada pacote em hexadecimal.

## Limites conhecidos

A telemetria do jogo não inclui combustível, temperaturas, munição, posição no mapa nem outros aviões. O copiloto diz quando não tem o dado em vez de chutar. O [PLANO.md](PLANO.md) descreve como parte disso pode ser contornada.

## Tecnologias previstas

| Parte | Escolha |
|---|---|
| Linguagem | Python 3.12 |
| Telemetria | `asyncio` (UDP) |
| Transcrição | `faster-whisper` em CPU |
| Modelo de linguagem | Claude (API da Anthropic) |
| Síntese de voz | Piper |
| Push-to-talk | botão do joystick via `pygame` |

Transcrição e voz rodam em CPU para deixar a placa de vídeo livre para o VR.

## Requisitos

- Windows 10 ou 11
- IL-2 Sturmovik: Great Battles (Stalingrad ou posterior)
- Python 3.12
- Microfone e fone de ouvido (a partir da Fase 1)
- Chave da API da Anthropic (a partir da Fase 1)

## Roteiro

| Fase | Entrega | Estado |
|---|---|---|
| 0 | Captura e decodificação da telemetria UDP | Implementada |
| 1 | Conversa por voz, ainda sem dados do jogo | A fazer |
| 2 | Copiloto respondendo com dados reais do voo | A fazer |
| 3 | Alertas proativos | A fazer |
| 4 | Checklists guiados e demais variantes do Bf 109 | A fazer |
| 5 | Navegação e leitura de tela (opcional) | A fazer |

## Aviso

Projeto pessoal, sem vínculo com a 1C Game Studios nem com os desenvolvedores do IL-2 Sturmovik. Todas as marcas pertencem aos seus donos.
