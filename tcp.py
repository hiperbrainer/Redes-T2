'''
PRÁTICA 2 - REDES DE COMPUTADORES

ALUNOS:
    - Brainer Sueverti de Campos - 790829
    - Rafael da Silva Ferreira Alves - 810996
'''

'''
1. Bibliotecas necessárias:
    - asyncio: fornece uma infraestrutura para escrever código usando a sintaxe async/await.
    - random: fornece funções para gerar números pseudo-aleatórios.
    - tcputils: valores e funções já declardados para a prática.
    - time: fornece funções para manipulação de tempo.
'''
import asyncio
import random
from tcputils import *
import time

'''
2. Classes: 
    - servidor: classe que representa um servidor de conexão.
    - ConexaoRDT: classe que representa uma conexão RDT.
'''

'''
2.1. Servidor:
    - __init__: inicializa o servidor com a rede e a porta especificadas.
    - registrar_monitor_de_conexoes_aceitas: registra uma função de callback que será invocada quando uma nova conexão for aceita.
    - _rdt_rcv: método interno responsável por processar os segmentos recebidos pela rede.
'''
class Servidor:
    def __init__(self, rede, porta):
        # Inicializa o servidor com a rede e a porta especificadas.
        self.rede = rede  # Objeto de rede responsável pela comunicação.
        self.porta = porta  # Porta na qual o servidor escutará por conexões.
        self.conexoes = {}  # Dicionário para armazenar conexões ativas, indexadas por seus identificadores.
        self.callback = None  # Função de callback a ser chamada quando uma nova conexão for aceita.
        # Registra o método _rdt_rcv como o receptor de segmentos da rede.
        # Este método será chamado sempre que um segmento de dados chegar à rede.
        self.rede.registrar_recebedor(self._rdt_rcv)

    def registrar_monitor_de_conexoes_aceitas(self, callback):
        # Registra uma função de callback que será invocada quando uma nova conexão for aceita.
        # Isso permite que o código externo seja notificado e lide com novas conexões.
        self.callback = callback

    def _rdt_rcv(self, src_addr, dst_addr, segment):
        # Método interno responsável por processar os segmentos recebidos pela rede.
        # Ele lida com o estabelecimento de conexões e a transmissão de dados.

        # Desmonta o cabeçalho do segmento para extrair as informações relevantes.
        src_port, dst_port, seq_no, ack_no, flags, window_size, checksum, urg_ptr = read_header(segment)
        
        #print("\033[96m {}\033[00m".format("Chamada ao _rdt_rcv de Servidor"))

        # Verifica se o segmento é destinado à porta do servidor.
        # Se não for, ignora o segmento.
        if dst_port != self.porta:
            return
        
        # Valida o checksum do segmento para garantir a integridade dos dados.
        # Se o checksum estiver incorreto, o segmento é descartado.
        if not self.rede.ignore_checksum and calc_checksum(segment, src_addr, dst_addr) != 0:
            print('Descartando segmento com checksum incorreto')
            return

        # Extrai o payload (dados) do segmento, ignorando o cabeçalho.
        payload = segment[4*(flags >> 12):]
        id_conexao = (src_addr, src_port, dst_addr, dst_port)

        # Verifica se o segmento é um pedido de conexão (flag SYN setada).
        if (flags & FLAGS_SYN) == FLAGS_SYN:
            # Cria uma nova conexão e responde com um segmento SYN+ACK para o cliente.
            conexao = self.conexoes[id_conexao] = ConexaoRDT(self, id_conexao)
            conexao.ack_no = new_ack_no = seq_no + 1  # Número de ACK a ser enviado.
            conexao.seq_no = new_seq_no = random.randint(0, 0xffff)  # Número de sequência inicial.
            conexao.flags = new_flags = FLAGS_SYN | FLAGS_ACK  # Flags de SYN+ACK.
            
            # Monta o cabeçalho de resposta e calcula o checksum.
            header = make_header(dst_port, src_port, new_seq_no, new_ack_no, new_flags)
            new_segment = fix_checksum(header, dst_addr, src_addr)
            
            # Envia o segmento de resposta (SYN+ACK) de volta ao cliente.
            self.rede.enviar(new_segment, src_addr)
            
            # Chama a função de callback registrada, notificando sobre a nova conexão.
            if self.callback:
                self.callback(conexao)
        
        # Se o ID da conexão já existe, delega o processamento para o receptor RDT da conexão.
        elif id_conexao in self.conexoes:
            self.conexoes[id_conexao]._rdt_rcv(seq_no, ack_no, flags, payload)
        
        # Se o segmento não pertence a nenhuma conexão conhecida, imprime uma mensagem de aviso.
        else:
            print('%s:%d -> %s:%d (pacote associado a conexão desconhecida)' %
                  (src_addr, src_port, dst_addr, dst_port))


'''
2.2. ConexaoRDT:
    - __init__: inicializa a conexão com o servidor e o identificador da conexão.
    - _rdt_rcv: método interno responsável por processar os segmentos recebidos pela rede.
    - registrar_recebedor: registra uma função de callback que será invocada quando dados forem recebidos.
    - enviar: envia dados para o destino da conexão.
    - timeout: método chamado quando ocorre um timeout no envio de um segmento.
    - intervalo_timeout_function: calcula o intervalo de timeout para retransmissão de segmentos.
    - fechar: envia um segmento de fechamento para encerrar a conexão.
'''

class ConexaoRDT:
    def __init__(self, servidor, id_conexao):
        # Inicializa a conexão com o servidor e o identificador da conexão (endereço e portas de origem/destino).
        self.servidor = servidor  # Referência ao objeto do servidor que criou esta conexão.
        self.id_conexao = id_conexao  # Identificador único da conexão, geralmente uma tupla de endereços e portas.
        self.callback = None  # Função de callback a ser chamada quando dados são recebidos.
        self.SampleRTT = None  # Tempo de ida e volta medido para um segmento.
        self.EstimatedRTT = None  # Estimativa do RTT com base em amostras anteriores.
        self.DevRTT = None  # Desvio estimado do RTT.
        self.alpha = 0.125  # Peso para o cálculo do RTT estimado.
        self.beta = 0.25  # Peso para o cálculo do desvio do RTT.
        self.not_check = []  # Lista para armazenar os segmentos enviados que ainda não foram confirmados pelo receptor.
        self.timer = None  # Temporizador para reenvio de segmentos em caso de perda.
        self.timer_ativo = False  # Indica se o temporizador está ativo.
        self.TimeoutInterval = 1  # Intervalo de timeout inicial, em segundos.
        self.received_acks = 0  # Contador de ACKs recebidos.
        self.segmentos_count = 0  # Contador de segmentos enviados.
        self.window = 1  # Tamanho da janela de congestionamento (quantos segmentos podem ser enviados antes de esperar por ACKs).
        self.window_ocupation = 0  # Número de segmentos enviados e ainda não confirmados (na janela).
        self.dados_restantes = bytearray()  # Buffer para armazenar dados que ainda precisam ser enviados.
        self.ack_no = None  # Número de reconhecimento para a próxima mensagem esperada.
        self.seq_no = None  # Número de sequência para a próxima mensagem a ser enviada.
        self.flags = None  # Flags do cabeçalho TCP (SYN, ACK, FIN, etc.).

    def _rdt_rcv(self, seq_no, ack_no, flags, payload):
        # Método interno que processa os segmentos recebidos, verifica a sequência e gerencia o fluxo de dados.
        tempo_recepcao_ack = time.time()  # Marca o tempo de recebimento do ACK para cálculo de RTT.

        # Verifica se o número de sequência recebido corresponde ao esperado.
        if seq_no != self.ack_no:
            #print("\033[91m {}\033[00m".format("Sequência incorreta"))
            #print("\033[96m {}\033[00m".format(f"Fim da função em {time.time()}"))
            return  # Sai do método se a sequência estiver incorreta.

        # Verifica se o segmento contém um pedido de fechamento de conexão (flag FIN).
        if (flags & FLAGS_FIN) == FLAGS_FIN:
            #print("\033[91m {}\033[00m".format("Recebendo flag de fechamento"))
            
            payload = b""  # Nenhum dado adicional é esperado com FIN.

            # Atualiza os números de sequência e reconhecimento para o fechamento da conexão.
            self.ack_no = novo_ack_no = seq_no + 1 + len(payload)
            self.seq_no = novo_seq_no = ack_no
            self.flags = novas_flags = FLAGS_ACK  # Prepara um ACK para confirmar o fechamento.

            # Constrói e envia o segmento de ACK para o fechamento.
            (dst_addr, dst_port, src_addr, src_port) = self.id_conexao
            header = make_header(src_port, dst_port, novo_seq_no, novo_ack_no, novas_flags)
            segmento = fix_checksum(header, src_addr, dst_addr)
            self.servidor.rede.enviar(segmento, dst_addr)

            # Chama o callback para notificar o recebimento do FIN.
            self.callback(self, payload)
            #print("\033[96m {}\033[00m".format(f"Fim da função em {time.time()}"))
            return

        # Verifica se o segmento é um ACK e não contém payload.
        if (flags & FLAGS_ACK) == FLAGS_ACK and len(payload) == 0:
            #print("\033[91m {}\033[00m".format("ACK recebido"))

            # Atualiza os números de sequência e reconhecimento.
            self.ack_no = seq_no
            self.seq_no = ack_no

            # Se há segmentos na lista de não confirmados:
            if self.not_check:
                # Calcula o novo intervalo de timeout baseado no tempo de recepção do ACK.
                self.intervalo_timeout_function(tempo_recepcao_ack)
                self.received_acks += 1  # Incrementa o contador de ACKs recebidos.
                self.window += 1  # Aumenta o tamanho da janela de congestionamento.
                # Cancela o temporizador ativo e remove o segmento confirmado da lista.
                self.timer.cancel()
                self.timer_ativo = False
                self.not_check.pop(0)
                self.window_ocupation -= 1  # Decrementa a ocupação da janela.
                #print("\033[91m {}\033[00m".format(f"ACKs recebidos -> {self.received_acks}"))
                #print("\033[91m {}\033[00m".format(f"Tamanho dos dados que ainda precisam ser enviados -> {len(self.dados_restantes)} ou {len(self.dados_restantes) / MSS}"))

            # Se ainda restam segmentos não confirmados, reativa o temporizador para o próximo timeout.
            if self.not_check:
                self.timer = asyncio.get_event_loop().call_later(self.TimeoutInterval, self.timeout)
                self.timer_ativo = True

            
            #print("\033[91m {}\033[00m".format(f"Ainda faltam {len(self.not_check)} seguimentos receber ACK."))
            return
        
        #print("\033[91m {}\033[00m".format("Segmento reconhecido"))
        #print("\033[91m {}\033[00m".format("xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"))

        # Se o segmento é válido, envia um ACK para confirmar o recebimento.
        self.ack_no = novo_ack_no = seq_no + len(payload)  # Calcula o próximo ACK esperado.
        self.seq_no = novo_seq_no = ack_no  # Prepara o próximo número de sequência.
        self.flags = novas_flags = FLAGS_ACK  # Seta a flag ACK.

        # Constrói e envia o segmento de ACK.
        (dst_addr, dst_port, src_addr, src_port) = self.id_conexao
        header = make_header(src_port, dst_port, novo_seq_no, novo_ack_no, novas_flags)
        segmento = fix_checksum(header, src_addr, dst_addr)
        self.servidor.rede.enviar(segmento, dst_addr)

        # Chama o callback com os dados do segmento recebido.
        self.callback(self, payload)


    def registrar_recebedor(self, callback):
        # Registra uma função de callback que será chamada sempre que um segmento for recebido.
        self.callback = callback

    def enviar(self, dados):
        # Envia os dados para o destinatário, dividindo-os em segmentos e gerenciando o envio e retransmissão.
        
        #print("\033[95m {}\033[00m".format("Enviando dados"))
        #print("\033[95m {}\033[00m".format(f"Janela de tamanho {self.window}"))

        (dst_addr, dst_port, src_addr, src_port) = self.id_conexao  # Extrai endereços e portas da conexão.
        buffer = dados  # Carrega os dados a serem enviados no buffer.

        # Envia os dados em segmentos até que o buffer esteja vazio.
        while len(buffer) > 0:
            self.segmentos_count += 1  # Incrementa o contador de segmentos enviados.
            #print("\033[95m {}\033[00m".format(f"-> Enviando segmento {self.segmentos_count}"))

            payload = buffer[:MSS]  # Separa o próximo pedaço de dados de acordo com o tamanho máximo de segmento (MSS).
            buffer = buffer[MSS:]  # Remove o pedaço enviado do buffer.

            # Cria um cabeçalho para o segmento e calcula o checksum.
            header = make_header(src_port, dst_port, self.seq_no, self.ack_no, FLAGS_ACK)
            segmento = fix_checksum(header + payload, src_addr, dst_addr)

            # Envia o segmento para a rede.
            self.servidor.rede.enviar(segmento, dst_addr)

            # Atualiza o número de sequência para a próxima transmissão.
            self.seq_no += len(payload)

            # Agendar o timer para retransmissão no caso de timeout.
            self.timer = asyncio.get_event_loop().call_later(self.TimeoutInterval, self.timeout)
            self.timer_ativo = True

            # Adiciona o segmento à lista de não confirmados, junto com o endereço de destino e o timestamp atual.
            self.not_check.append([segmento, dst_addr, time.time()])

        # Atualiza a ocupação da janela e armazena os dados restantes no buffer.
        self.window_ocupation += self.window
        self.dados_restantes = bytearray()
        self.dados_restantes.extend(buffer)

    def timeout(self):
        # Método chamado quando ocorre um timeout para retransmitir o primeiro segmento não confirmado.
        
        if self.not_check:
            #print("\033[93m {}\033[00m".format("--> Timeout! Reenviando segmento..."))

            # Reduz o tamanho da janela pela metade como resposta ao timeout.
            self.window = self.window - self.window // 2
            #print("\033[93m {}\033[00m".format(f"Janela de tamanho {self.window}"))

            # Reenvia o primeiro segmento na lista de não confirmados.
            segmento = self.not_check[0][0]
            dst_addr = self.not_check[0][1]
            self.servidor.rede.enviar(segmento, dst_addr)

            # Agenda o timer para a próxima tentativa de retransmissão.
            self.timer = asyncio.get_event_loop().call_later(self.TimeoutInterval, self.timeout)
            self.timer_ativo = True

            # Atualiza o timestamp do segmento reenviado.
            self.not_check[0][2] = None

    def intervalo_timeout_function(self, tempo_recepcao_ack):
        # Calcula o intervalo de timeout com base no RTT (Round-Trip Time) estimado e seu desvio.
        tempo_envio_seq = self.not_check[0][2]  # Obtém o tempo de envio do segmento não confirmado.
        # Calcula a amostra de RTT.
        if tempo_envio_seq:
            self.SampleRTT = tempo_recepcao_ack - tempo_envio_seq  # Calcula o RTT amostrado.
        else:
            return False
        # Se o RTT estimado ainda não foi inicializado, inicializa com a primeira amostra.
        if self.EstimatedRTT is None:
            self.DevRTT = self.SampleRTT / 2  # Inicializa o desvio com metade do RTT amostrado.
            self.EstimatedRTT = self.SampleRTT
        else:
            # Atualiza o RTT estimado 
            self.EstimatedRTT = (1 - self.alpha) * self.EstimatedRTT + self.alpha * self.SampleRTT
            self.DevRTT = (1 - self.beta) * self.DevRTT + self.beta * abs(self.SampleRTT - self.EstimatedRTT)
        
        # Calcula o intervalo de timeout com base no RTT estimado e no desvio.
        self.TimeoutInterval = self.EstimatedRTT + 4 * self.DevRTT
        return True

    def fechar(self):
        # Encerra a conexão, enviando um segmento com a flag de fechamento (FIN).
        
        #print("\033[93m {}\033[00m".format("Mandando flag de fechamento"))

        (dst_addr, dst_port, src_addr, src_port) = self.id_conexao  # Extrai endereços e portas da conexão.
        header = make_header(src_port, dst_port, self.seq_no, self.ack_no, FLAGS_FIN)  # Cria o cabeçalho com a flag FIN.
        segmento = fix_checksum(header, src_addr, dst_addr)  # Calcula o checksum do segmento.

        # Envia o segmento de fechamento para a rede.
        self.servidor.rede.enviar(segmento, dst_addr)

        # Chama o callback para notificar o fechamento da conexão.
        self.callback(self, b"")

        # Remove a conexão da lista de conexões ativas do servidor.
        del self.servidor.conexoes[self.id_conexao]