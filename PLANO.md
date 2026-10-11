# Gustav: copiloto de IA para IL-2 Sturmovik — Plano do Projeto

## 1. Objetivo

Um copiloto de IA com quem você conversa por voz enquanto voa. Ele enxerga o estado do avião em tempo real, responde perguntas, lê checklists, avisa de problemas antes que virem acidente e conhece os limites de cada aeronave.

Exemplos do que deve funcionar:

- "Qual a potência de subida desse avião?" → responde com manifold/RPM do avião atual.
- "Me passa o checklist de pouso." → lê passo a passo, conferindo trem e flaps pela telemetria.
- (sem você pedir) "Trem ainda recolhido, 150 metros." / "Cinco minutos em potência de emergência."
- "Como eu abro o radiador de óleo no 109?" → explica o sistema e a tecla.

## 2. Premissas

| Premissa | Padrão adotado | Se for diferente |
|---|---|---|
| Versão do jogo | IL-2 Great Battles, instalado em `E:\SteamLibrary\steamapps\common\IL-2 Sturmovik Battle of Stalingrad` | Confirmado |
| Aviões | Família Bf 109 (o jogo traz E-4, E-7, F-2, F-4, G-2, G-4, G-6, G-6 Late, G-6AS, G-14, K-4) | Confirmado |
| Exibição | VR (ativo no `startup.cfg`, 2528×2704 por olho) | Confirmado |
| Papel do copiloto | Só aconselha; nunca aperta teclas nem envia comandos ao jogo | Confirmado |
| Sistema | Windows 11, tudo rodando no mesmo PC do jogo | — |
| Idioma da conversa | Português do Brasil; jogo em inglês | — |
| Linguagem | Python 3.11 ou mais novo | — |

### O que o VR muda

- **GPU sem folga.** Nessa resolução o jogo usa a placa inteira. A transcrição roda em CPU ou em nuvem, nunca na GPU.
- **Você não vê o teclado.** Tudo passa por voz e pelo HOTAS. Perguntar "qual tecla faz X?" vira uma das funções mais úteis.
- **Áudio no headset.** A fala do copiloto sai no dispositivo de áudio do VR, mixada com o jogo, com volume próprio.
- **Captura de tela pouco confiável.** O espelho no monitor mostra para onde sua cabeça aponta, não os instrumentos. A leitura por visão (Fase 5) perde prioridade.

### O que o Bf 109 muda

- O 109 automatiza passo de hélice, radiadores e mistura. Sobra para o piloto exatamente o que a telemetria cobre: pressão de admissão (ata), RPM, velocidade, flaps e trem.
- O principal risco de quebrar o motor é estourar o tempo em potência de emergência ou de combate. O copiloto cronometra isso sozinho a partir de ata e RPM.
- Outros pontos da família que entram na base: estabilizador e flaps manuais por volante, trava da bequilha, guinada na decolagem, trem estreito no pouso, MW 50 no G-14 e K-4, velocidades limite de flaps, trem e mergulho.
- Unidades sempre métricas: km/h, metros, ata.

## 3. Arquitetura

```
 Microfone ──► PTT (botão do HOTAS) ──► STT (Whisper local)
                                              │
 IL-2 ──UDP──► Coletor de telemetria ──► Estado do voo ──► Cérebro (Claude + ferramentas) ──► TTS ──► Fone
                                              │                    ▲
                                              ▼                    │
                                      Motor de alertas ────────────┘
                                      (regras, sem LLM)
                                                           Base de conhecimento
                                                           (aviões, checklists, teclas)
```

Cinco módulos independentes, ligados por uma fila de eventos interna:

1. **Coletor de telemetria** — escuta o UDP do jogo e mantém um objeto "estado do voo" atualizado.
2. **Motor de alertas** — regras determinísticas sobre o estado do voo. Não passa pelo LLM, por isso fala em menos de 1 segundo.
3. **Voz (entrada)** — push-to-talk + transcrição.
4. **Cérebro** — Claude com ferramentas para consultar o estado do voo e a base de conhecimento.
5. **Voz (saída)** — síntese de fala com fila de prioridade (alerta interrompe conversa).

## 4. Dados em tempo real: o que o jogo entrega

O Great Battles envia telemetria por UDP quando habilitado em `data/startup.cfg`. O seu arquivo hoje não tem essas duas seções, então a telemetria está desligada. Elas precisam ser acrescentadas com o jogo fechado, porque ele regrava o arquivo ao sair:

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

**Disponível pela telemetria (confirmado na Fase 0, voo de 08/10/2026 em Bf 109 F-4):**

Os dois canais chegam a 25 Hz (tick do jogo a 50 Hz, `decimation = 2`), sem erro de decodificação em 11 minutos.

| Campo | Unidade e comportamento medido |
|---|---|
| RPM | rpm, um valor por motor |
| Pressão de admissão | Pa, um valor por motor. Para ata, dividir por 101 325 (atmosfera padrão): assim os patamares batem com 1,42 ata a 2700 rpm e 1,15 ata a 2300 rpm |
| Velocidade indicada | m/s |
| Ângulo de ataque | rad. Só é confiável em voo; no chão e em baixa velocidade oscila entre ±180° |
| Altura sobre o solo | m, medida de um ponto do avião: marca de 0,9 a 1,9 m com as rodas no chão |
| Trem de pouso | 0 = recolhido, 1 = baixado, um valor por perna (esquerda, direita, bequilha), com valores intermediários durante o curso |
| Compressão do trem | 0 a 1 por perna; serve como "peso nas rodas". A bequilha fica parada em 0,17 em voo |
| Flaps | 0 a 1, contínuo (volante manual do 109) |
| Freios aerodinâmicos | Sempre 0 no 109 |
| Aceleração | m/s² nos eixos do avião, com a gravidade incluída: Y é o vertical (9,8 parado). Carga G = Y / 9,81 |
| Vibração do motor e da cabine | Frequência e amplitude; sem uso previsto |
| Atitude (canal de movimento) | Guinada, arfagem e rolagem em rad, taxas de rotação em rad/s, aceleração em m/s² |

Eventos, com a numeração real (um a mais do que a documentação antiga indicava):

| ID | Evento | Estado |
|---|---|---|
| 0 | Nome do avião em texto ("Bf 109 F-4") | Confirmado. Permite carregar a base do avião certo sem perguntar |
| 1 | Motor: posição e RPM máximo (2800 no F-4) | Confirmado |
| 2 | Arma: posição, massa do projétil e velocidade de boca | Confirmado |
| 3 | Perna do trem: posição | Confirmado |
| 4 a 8 | Bomba, foguete, impacto, dano, explosão | Não ocorreram no voo; layout presumido |
| 9 | Disparo, com o índice da arma | Confirmado |
| 13 | Dados do jogador (traz o nome) | Layout parcial |
| 14 | Desconhecido, 6 bytes | — |

Os eventos 0 a 3 e 14 se repetem a cada 10 segundos. O tick volta a zero quando a missão recomeça, o que serve para detectar um voo novo.

**Ainda sem validação:** o trem cedeu no pouso do voo de teste e nenhum evento de dano chegou, então o alerta de "dano recebido" da Fase 3 depende de um voo com dano de combate para confirmar os eventos 6 e 7.

**Não disponível pela telemetria:**

- Posição no mapa e rumo para o objetivo
- Combustível, temperaturas de água/óleo, munição restante
- Posição de inimigos e aliados

Essa lacuna define o que o copiloto consegue ou não fazer. Formas de cobri-la, em ordem de custo:

| Lacuna | Solução | Fase |
|---|---|---|
| Temperaturas, combustível, munição | Estimativa por modelo simples (tempo em potência alta, consumo por regime) + você informar por voz | 3 |
| Eventos da missão (decolagem, abates, danos) | Log de texto da missão, ligando `mission_text_log = 1` no `startup.cfg` (hoje está 0) | 4 |
| Posição e navegação | Leitura do arquivo da missão para briefing e waypoints | 5 |
| Mesmos dados, com precisão | Captura do espelho do VR + leitura por visão; pouco confiável, só se o resto não bastar | 5 |
| Tráfego | Fora do escopo: não há fonte legítima | — |

## 5. Stack técnica

| Componente | Escolha | Motivo |
|---|---|---|
| Telemetria | `asyncio` UDP + `struct` | Protocolo binário simples, sem dependência |
| PTT | Tecla global lida com `GetAsyncKeyState` (padrão: `0` do teclado numérico) | Funciona com o jogo em foco e sem dependência; botão do HOTAS pode ser mapeado para a tecla |
| Captura de áudio | `sounddevice` | Baixa latência no Windows |
| STT | `faster-whisper` modelo `small` em CPU (int8) | Local e sem custo, deixa a GPU livre para o VR. Se a CPU não der conta junto com o jogo, STT em nuvem |
| LLM | Claude API, streaming, com tool use e prompt caching | `claude-haiku-4-5-20251001` para respostas rápidas em voo; `claude-opus-5-5` para perguntas complexas e visão |
| TTS | Piper (local, voz PT-BR, em CPU), saída no dispositivo de áudio do headset | Latência baixa e offline. ElevenLabs como opção de voz melhor |
| Base de conhecimento | Arquivos YAML/Markdown por avião | Simples de editar; cabe inteira no prompt com cache |
| Config | `config.toml` | Portas, dispositivo de áudio, botão do PTT, avião |

**Meta de latência:** do soltar do PTT até o início da fala, até 2 segundos. Alertas do motor de regras, até 0,5 segundo. Para isso: streaming do LLM com TTS frase a frase, respostas curtas por instrução de sistema e frases de alerta pré-sintetizadas.

## 6. Ferramentas expostas ao Claude

**Decisão da Fase 2:** o estado do voo, a ficha do avião, os checklists e as teclas não são ferramentas. O retrato dos instrumentos vai junto de cada pergunta, e a ficha e as teclas ficam no prompt de sistema, com cache. Cada chamada de ferramenta custaria uma ida e volta a mais ao modelo (cerca de 1 s), e a conversa já está no limite da meta de 2 s. Ferramentas ficam para o que precisa de ação: `set_timer` e `set_alert` na Fase 4 e `capture_screen` na Fase 5.

Lista original, para referência:

- `get_flight_state()` — retrato atual: velocidade, AGL, RPM, admissão, trem, flaps, G, atitude.
- `get_flight_history(segundos)` — tendência recente (subindo? perdendo velocidade?).
- `get_aircraft_data(topico)` — limites de motor, velocidades, procedimentos do avião atual.
- `get_checklist(nome)` — decolagem, subida, cruzeiro, combate, pouso.
- `get_keybind(acao)` — tecla configurada, lida do arquivo de controles do jogo.
- `set_timer(nome, duracao)` / `set_alert(condicao)` — "me avisa quando passar de 3000 metros".
- `capture_screen(regiao)` — fase 5, para ler instrumentos ou mapa.

O prompt de sistema define o personagem: copiloto de rádio, frases curtas, unidades do avião atual, nunca inventa valor que a telemetria não fornece (diz que não tem o dado).

## 7. Fases

### Fase 0 — Validação da telemetria (concluída em 08/10/2026)
- Habilitar UDP no `startup.cfg`, capturar pacotes e decodificar.
- Documentar exatamente quais campos chegam, unidades e frequência.
- **Critério de saída:** script que imprime velocidade, AGL, RPM, trem e flaps ao vivo durante um voo.
- Esta fase decide o resto: se algum campo esperado não existir, as fases 3 e 5 são ajustadas.

### Fase 1 — Conversa por voz, sem jogo (concluída em 11/10/2026)

Validada em VR: perda de FPS pequena; transcrição em 1,2 a 1,4 s e início da fala entre 2,0 e 2,5 s depois de soltar a tecla.

- PTT no joystick → Whisper → Claude (streaming) → Piper.
- Medir latência ponta a ponta e ajustar modelo de STT.
- **Critério de saída:** conversa fluida em PT-BR com o jogo rodando em VR, sem queda de FPS nem reprojeção perceptível.

### Fase 2 — Copiloto com dados reais (implementada; falta validar em voo e revisar as fichas)

Testada com o voo gravado reproduzido pelo `tools/replay.py`. As fichas do F-4 e do G-2 foram escritas de memória, porque as especificações do jogo estão em arquivos empacotados: os valores marcados "aprox." precisam ser conferidos com a ficha que o jogo mostra.

- Ligar coletor + estado do voo às ferramentas do Claude.
- Base de conhecimento para o Bf 109 F-4 e o G-2, montada a partir das especificações do próprio jogo. As outras variantes entram na Fase 4.
- Leitura do arquivo de controles em `data/input` para responder "qual tecla faz X?".
- **Critério de saída:** perguntar "como está o motor?" em voo e receber resposta correta com os números reais.

### Fase 3 — Alertas proativos (3–4 dias)
- Motor de regras: trem recolhido em aproximação, estol iminente (AoA), excesso de G, tempo em potência de emergência/combate, flaps acima da velocidade limite, dano recebido, altura baixa em mergulho.
- Prioridades, tempo mínimo entre repetições e comando de voz "silêncio" para o combate.
- **Critério de saída:** alertas corretos em 10 voos de teste, sem falsos positivos irritantes.

### Fase 4 — Checklists e procedimentos guiados (2–3 dias)
- Checklists interativos que avançam sozinhos quando a telemetria confirma o passo.
- Temporizadores e alertas personalizados por voz.
- Completar a base com as demais variantes do Bf 109 (E-4, E-7, F-2, G-4, G-6, G-6 Late, G-6AS, G-14, K-4).
- Ler o log de texto da missão para eventos e debriefing pós-voo.

### Fase 5 — Navegação e visão (opcional, 1–2 semanas)
- Leitura do arquivo da missão para briefing e waypoints.
- Captura do espelho do VR para ler technochat ou mapa, só se as fases anteriores mostrarem que faz falta.

## 8. Estrutura do repositório

```
gustav/
├── PLANO.md
├── config.toml
├── pyproject.toml
├── src/copilot/
│   ├── main.py              # orquestra os módulos
│   ├── telemetry/           # receptor UDP, decodificação, estado do voo
│   ├── alerts/              # motor de regras e regras por avião
│   ├── voice/               # ptt, stt, tts, fila de fala
│   ├── brain/               # cliente Claude, ferramentas, prompt
│   └── knowledge/           # carregamento da base
├── data/
│   ├── aircraft/            # um YAML por avião
│   └── checklists/
├── tools/
│   ├── sniff_telemetry.py   # fase 0
│   └── replay.py            # reproduz voo gravado para testar sem o jogo
└── tests/
```

O `replay.py` é importante: grava os pacotes UDP de um voo e reproduz depois, permitindo desenvolver e testar alertas sem abrir o jogo.

## 9. Riscos e decisões em aberto

| Item | Risco | Mitigação |
|---|---|---|
| Telemetria limitada | Sem combustível, temperatura e posição, parte das perguntas fica sem resposta | Fase 0 confirma; copiloto diz claramente quando não tem o dado; visão na fase 5 |
| Desempenho em VR | Qualquer disputa por GPU ou CPU vira reprojeção e enjoo | STT e TTS em CPU com prioridade baixa; medir tempo de quadro com e sem o copiloto; STT em nuvem como saída |
| Ruído no microfone | Som do motor atrapalha a transcrição | PTT obrigatório, fone fechado, vocabulário de aviação como dica para o Whisper |
| Latência do LLM | Resposta lenta é inútil em combate | Haiku para o caminho rápido, alertas fora do LLM, respostas de uma frase |
| Custo da API | Uso contínuo em sessões longas | Prompt caching da base de conhecimento; LLM só é chamado quando você fala |
| Dados dos aviões | Limites errados na base levam a conselho errado | Montar a partir das especificações do próprio jogo e revisar variante por variante |
| Edição do `startup.cfg` | Erro no arquivo pode impedir o jogo de abrir | Fazer cópia de segurança antes; editar só com o jogo fechado |

Apertar teclas pelo copiloto está fora do escopo por decisão sua.

## 10. Próximo passo

As Fases 0 e 1 estão concluídas e a Fase 2 está implementada. Para fechá-la:

1. Voar com o copiloto ligado e perguntar "como está o motor?" em regimes diferentes, conferindo com os instrumentos.
2. Conferir as fichas em `data/aircraft/` com a ficha do F-4 e do G-2 no jogo e corrigir os valores marcados "aprox.".
3. Depois, começar a Fase 3 (alertas proativos), incluindo o cronômetro de tempo em potência de emergência e de combate.
