# Controlart Nexo Hub para Home Assistant

Integração do Home Assistant para o sistema **Nexo Hub** (Relé e Dimmer), da Controlart.

O Home Assistant mostra **só os itens marcados** na tela "Enviar dispositivos para comando de voz" do configurador do Nexo, com o nome falado, e comanda cada um **do mesmo jeito que o comando de voz chega** ao hub. Funciona pela rede local, sem nuvem e sem conta.

Quem escolhe o que vai para o Home Assistant é o integrador, no configurador do Nexo, na mesma tela usada para a Alexa e o Google. O que está marcado lá aparece no Home Assistant; o que não está, não aparece e não pode ser comandado por ele.

## O que aparece no Home Assistant

| Marcado para voz | No Home Assistant | O que dá para fazer |
|---|---|---|
| Relé | Luz | Ligar e desligar |
| Dimmer | Luz com brilho | Ligar, desligar e escolher o brilho |
| Cortina | Cortina | Abrir, fechar e escolher a posição (100 % = aberta) |
| Cena | Cena | Disparar |

- Cada item vira um dispositivo com o **nome falado** da lista.
- Relés, dimmers e cortinas entram na **área** com o nome do ambiente do configurador. Se a área ainda não existir, o Home Assistant a cria. Cenas ficam sem área.
- Cada hub que guarda uma lista aparece também como um dispositivo, e os itens da lista dele ficam ligados a ele.

## Limites (os mesmos do comando de voz)

- Cortina não tem "parar" e precisa estar **calibrada** no configurador para aceitar comando.
- Dimmer ligado sem escolher o nível acende em 100 %.
- Relé de acionamento (bomba, portão etc.) aparece como luz.
- **Marque os itens num Nexo Hub Relé.** No Nexo Hub Dimmer, a memória dos níveis grava no mesmo espaço da lista de voz e a estraga alguns segundos depois de uma mudança de nível. É um limite do firmware do dimmer. A integração reconhece a lista estragada e a ignora. Os itens do dimmer podem ser marcados na lista do relé.

## Requisitos

- Nexo Hub na mesma rede do Home Assistant, com IP fixo ou reservado no roteador.
- O Home Assistant alcançando os hubs nas portas **80** (HTTP, para os ambientes) e **4998** (TCP, para a lista, o estado e os comandos).
- Itens marcados na tela de comando de voz do configurador (passo 1 abaixo).

## Passo 1: marcar os itens no configurador do Nexo

1. No navegador, abra o IP do **Nexo Hub Relé** que vai guardar a lista (de preferência o principal da casa) e entre com o usuário e a senha do configurador.
2. Abra o menu (☰). Se **Enviar dispositivos para comando de voz** não estiver na lista, ligue a comunicação remota primeiro: **Configurações gerais → Autenticação e Nuvem → Comunicação remota**. A tela só aparece com esse hub conectado ao servidor da Controlart.
3. Abra **Enviar dispositivos para comando de voz**. A aba **Circuitos** lista as saídas de todos os hubs da casa; a aba **Cenas** lista as cenas.
4. Marque a caixa de cada item que deve ir para o Home Assistant. Para marcar ou desmarcar a aba toda, use a caixa do cabeçalho.
5. Confira o **Nome falado**: é o nome que a entidade terá no Home Assistant. Nomes repetidos ganham um número (`Luz`, `Luz_2`).
6. Cada alteração é gravada sozinha no hub, um segundo e meio depois da última.

Para o Home Assistant **não** é preciso clicar em **Enviar para assistentes de voz e IA**. Esse botão manda a lista para a Alexa e o Google. A integração lê a lista gravada no hub.

**Atualizar circuitos** relê os hubs. Use depois de criar ou renomear saídas.

## Passo 2: instalar a integração

### Pelo HACS

[![Abrir no HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fabian-rezende&repository=nexo-homeassistant&category=integration)

Ou, à mão:

1. HACS → menu de três pontos → **Repositórios personalizados**.
2. Cole `https://github.com/fabian-rezende/nexo-homeassistant`, tipo **Integração**, e adicione.
3. Abra **Controlart Nexo Hub** e clique em **Baixar**.
4. Reinicie o Home Assistant.

Não tem o HACS? O passo a passo completo, desde a instalação do HACS, está em [`instalacao-hacs.html`](instalacao-hacs.html).

Versões novas aparecem no HACS como atualização. Depois de atualizar, reinicie o Home Assistant.

### Manual

Copie a pasta `custom_components/nexohub` para `<config>/custom_components/nexohub` e reinicie o Home Assistant.

## Passo 3: adicionar o sistema no Home Assistant

1. **Configurações → Dispositivos e serviços → Adicionar integração → Controlart Nexo Hub**.
2. Informe o IP de **qualquer** Nexo Hub da casa. A integração passa pelos outros hubs sozinha e lê a lista de cada um.
3. No final, o Home Assistant mostra os dispositivos criados e deixa escolher as áreas.

Um Nexo Hub também pode aparecer em **Descobertos** quando pede endereço à rede. Nesse caso, basta confirmar.

A casa entra uma vez só. Tentar adicionar de novo pelo IP de outro hub da mesma casa avisa que o sistema já está configurado.

## Depois de instalado

- **Marcar ou desmarcar** um item no configurador muda o Home Assistant em até 5 minutos. Desmarcado, o item some; marcado, aparece.
- **Renomear** o nome falado também chega em até 5 minutos.
- **Tecla na parede, cena ou app** mudando um circuito aparece no Home Assistant na hora.
- **Hub desligado ou fora da rede:** os itens da lista dele ficam indisponíveis e voltam sozinhos quando ele volta.

## Quando algo não funciona

| O que aparece | O que fazer |
|---|---|
| "O hub não respondeu nesse endereço" | Confira o IP e se o Home Assistant alcança o hub nas portas 80 e 4998. |
| "Nenhum hub da casa tem itens marcados para comando de voz" | Faça o passo 1 e confira que ao menos um item ficou marcado. |
| Item marcado não aparece | Espere 5 minutos ou recarregue a integração (Dispositivos e serviços → Controlart Nexo Hub → ⋮ → Recarregar). Se a lista estiver num dimmer, veja os limites acima. |
| Cortina não obedece | Calibre a cortina no configurador do Nexo. |
| Entidade indisponível | O hub que guarda a lista dela está fora da rede. |

## Como funciona

- As listas vêm dos hubs, as mesmas usadas pela Alexa e pelo Google. A integração lê a de todos os hubs da casa e junta os itens; o mesmo circuito marcado em duas listas aparece uma vez só.
- Cada comando vai, no formato do comando de voz e pelo identificador do item, ao hub que guarda a lista daquele item; ele repassa ao hub dono do circuito. Item desmarcado é recusado pelo próprio hub.
- O estado vem da memória dos hubs (lida a cada minuto e logo depois de cada comando) e dos avisos de mudança da malha, que chegam na hora.
- Tudo pela rede local: TCP 4998 (JSON por linha) e `GET /appdata` na porta 80.
