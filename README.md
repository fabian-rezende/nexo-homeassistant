# Controlart Nexo Hub para Home Assistant

Integração do Home Assistant para o sistema **Nexo Hub** (Relé e Dimmer), da Controlart.

O Home Assistant mostra **só os itens marcados** na tela "Enviar dispositivos para comando de voz" do configurador do Nexo, com o nome falado, e comanda cada um **do mesmo jeito que o comando de voz chega** ao hub. Funciona pela rede local, sem nuvem e sem conta.

A tela de comando de voz pode ser aberta em qualquer hub (relé ou dimmer), e cada um guarda a sua lista. A integração lê a lista de **todos** os hubs da casa e junta os itens; o mesmo circuito marcado em duas listas aparece uma vez só.

## O que aparece no Home Assistant

| Marcado para voz | No Home Assistant |
|---|---|
| Relé | Luz (liga/desliga) |
| Dimmer | Luz com brilho |
| Cortina | Cortina (abrir, fechar e posição) |
| Cena | Cena |

Desmarcar um item no configurador tira a entidade do Home Assistant em até 5 minutos; marcar um novo faz ele aparecer no mesmo prazo.

## Limites (os mesmos do comando de voz)

- Cortina não tem "parar" e precisa estar **calibrada** no configurador para aceitar comando.
- Dimmer ligado sem escolher o nível acende em 100 %.
- Relé de acionamento (bomba, portão etc.) aparece como luz.

## Requisitos

- Nexo Hub na mesma rede do Home Assistant.
- Itens marcados na tela de comando de voz do configurador (em qualquer hub).

## Instalação

### Pelo HACS

[![Abrir no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fabian-rezende&repository=nexo-homeassistant&category=integration)

Ou, à mão:

1. HACS → menu de três pontos → **Repositórios personalizados**.
2. Cole `https://github.com/fabian-rezende/nexo-homeassistant`, tipo **Integração**, e adicione.
3. Abra **Controlart Nexo Hub** e clique em **Baixar**.
4. Reinicie o Home Assistant.

Não tem o HACS? O passo a passo completo está em [`instalacao-hacs.html`](instalacao-hacs.html).

### Manual

Copie a pasta `custom_components/nexohub` para `<config>/custom_components/nexohub` e reinicie o Home Assistant.

## Adicionar o sistema

**Configurações → Dispositivos e serviços → Adicionar integração → Controlart Nexo Hub** e informe o IP de **qualquer** Nexo Hub da casa. A integração encontra os outros hubs sozinha.

Um Nexo Hub também pode aparecer em "Descobertos" quando pedir endereço à rede.

## Como funciona

- As listas vêm dos hubs (as mesmas usadas pela Alexa e pelo Google).
- Cada comando vai, no formato do comando de voz e pelo identificador do item, ao hub que guarda a lista daquele item; ele repassa ao hub dono do circuito. Item desmarcado é recusado pelo próprio hub.
- O estado vem da memória dos hubs e dos avisos de mudança, então a tecla na parede aparece na hora.
