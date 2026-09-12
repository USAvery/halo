# System Link Architecture

Sources: `kb.json` function inventory, `artifacts/ntsc_callgraph/callgraph.json`, the networking source under `src/halo/networking/` and `src/halo/bungie_net/`, `docs/system-link-rng-desync.md`, `docs/networking_system_link_bug.md`.

# 1. Not ported yet

This table excludes `XNET:xnet.obj` and `XNET:wsock.obj` (Xbox-OS imports).

| State | Count | Meaning |
|---|---:|---|
| `ported:true` | 343 | active redirect |
| `ported:false` | 15 | C body exists, deactivated (runs original) |
| no port field | 23 | never lifted, thunks to original |

## 1a. Never lifted (23)

| Addr | Obj | Name | Role |
|---|---|---|---|
| `0x82310` | transport_endpoint_set_winsock | `create_endpoint_set` | allocate endpoint set |
| `0x824d0` | transport_endpoint_set_winsock | `poll_endpoint_set` | `select()` over sockets |
| `0x82700` | transport_endpoint_set_winsock | `add_endpoint_to_set` | FD_SET add |
| `0x82d70` | transport_endpoint_set_winsock | `get_next_endpoint_from_set` | alloc endpoint (type 0x11 UDP / 0x12 TCP) |
| `0x83e20` | transport_endpoint_set_winsock | `FUN_00083e20` | UDP bind (xnet_bind/ioctl) |
| `0x841b0` | transport_endpoint_set_winsock | `FUN_000841b0` | TCP connect/WSAConnect |
| `0x843a0` | transport_endpoint_set_winsock | `FUN_000843a0` | TCP listen |
| `0x84740` | transport_endpoint_set_winsock | `FUN_00084740` | UDP `sendto` |
| `0x82c90`,`0x82cf0` | transport_endpoint_set_winsock | — | endpoint thread helpers |
| `0x805a0`,`0x80620` | message_header | — | key-agreement TX/RX |
| `0x803d0` | message_header | `key_agreement_build_message` | key-agreement message builder |
| `0x80940` | message_header | `message_encrypt` | payload encryption |
| `0x80a40` | message_header | `message_decrypt` | payload decryption |
| `0x80d50` | message_header | `sieve_of_eratosthenes` | prime generation for key agreement |
| `0x131a20`,`0x131b60`,`0x131e00`,`0x131fc0`,`0x132460`(`flag_render_proper`),`0x132ea0` | telnet_console (kb.json label) | — | This is a CTF flag-widget cluster, not a telnet console. An `assert_halt` in this cluster cites the original path `objects/widgets/flags.c`. It is out of scope for system link. |
| `0x124730` | network_client_manager (kb.json label) | `FUN_00124730` | This is a model/marker function, not a networking function. Its declaration takes `model_ref, marker_name, magic_table, node_remap, node_count, node_matrices, mirrored, out_markers, max_markers`. |

`0x83930` (socket creator) is not on this list. It is `ported:false` and appears only in table 1b.

`message_encrypt` and `message_decrypt` are unlifted, even though Layer 3 below states their addresses for reference.

The transport functions above are the winsock primitives needed to move the socket layer off the Xbox OS. The connection logic that calls them (write, read, idle, endpoint-set delete, remove, rewind, count) is already ported.

## 1b. Lifted but deactivated (`ported:false`, all in `network_server_manager.obj` plus one socket function)

14 of 15 are in `tools/audit/deactivation_allowlist.json`. The listed reason is *"sub-80% VC71 lift; runs original binary behavior until improved past the 80% ship threshold"* (since 2026-07-05). `0x83930` is absent from the allowlist. `0x12f990` carries a different entry: "pre-existing committed deactivation (HEAD), unrecorded reason" (since 2026-06-21). All 15 functions run original bytes today. They are the primary surface for system-link debugging.

| Addr | Function | Role |
|---|---|---|
| `0x12eb20` | `network_game_server_start` | server main tick |
| `0x12eca0` | `network_server_manager_pregame_start` | postgame→pregame reset |
| `0x12e750` | `FUN_0012e750` | server pregame (state 0) tick |
| `0x12e580` | `FUN_0012e580` | handle client machines |
| `0x12d880` | `FUN_0012d880` | add new client connection |
| `0x12dc20` | `FUN_0012dc20` | set up variant/name/open game |
| `0x12f5d0` | `FUN_0012f5d0` | broadcast pregame game data |
| `0x12f690` | `network_game_server_reset_to_pregame` | advertise game (broadcast reply) |
| `0x12f8d0` | `FUN_0012f8d0` | client ping handler |
| `0x12f990` | `FUN_0012f990` | join-game-request handler |
| `0x12f040` | `FUN_0012f040` | client game-start request |
| `0x12f170` | `FUN_0012f170` | client loaded |
| `0x12f200` | `FUN_0012f200` | add player ingame |
| `0x12f290` | `FUN_0012f290` | remove player postgame |
| `0x83930` | socket creator | transport, same VC71 reason |

Two docs describe related regressions: `docs/networking_system_link_bug.md` (transport_error_connection_lost) and `docs/system-link-rng-desync.md` (open, no proven cause). Both predate some of the lifts above. Each names addresses as "not in kb.json", for example `0x128e00 network_connection_write` and `0x129cf0`. Those addresses are now ported.

Pure XDK networking is unported and out of scope: `XNET:xnet.obj` (15 functions) and `XNET:wsock.obj` (15 functions) run inside the static Xbox library.

# 2. Architecture

This section describes five layers, from the bottom up. Xbox system link uses server-authoritative deterministic lockstep. It does not use Gearbox's client-prediction netcode. (`docs/references/h1/pages/engine/netcode.md` describes the later PC port, not this build.)

```
main loop  (main.c:4509-4554)
 ├─ network_game_client_start_frame 0x12a2d0  (word_46DA0C==1|2)
 ├─ network_game_server_start_frame 0x12a210  (word_46DA0C==2)
 └─ in game: network_game_client_end_frame 0x12a500
game_time_update 0xb6020  — lockstep tick advance
```

`word_46DA0C`: 1 = client only, 2 = host (client+server), 3 = quit.

## Layer 1 — transport / winsock (`src/halo/bungie_net/`, Xbox XNET below)

Two translation units make up `transport_endpoint_set_winsock.obj`: `transport_endpoint_set_winsock.c` and `transport_endpoint_winsock.c`.
- An endpoint set is a socket file-descriptor array, with operations `create`, `delete`, `add`, `remove`, `rewind`, `count`, `poll`, and `get_next`.
- The endpoint pool has a cleanup function, `endpoint_pool_cleanup`. Endpoints support create, bind, connect, listen, and accept operations.
- The API includes `recv_endpoint` (0x82e50), `send_endpoint` (0x82f50), `close_endpoint` (0x84000), the datagram functions `FUN_00084520` (recvfrom) and `FUN_00084740` (sendto, unported), and `transport_server_initialize`/`transport_server_terminate`.
- `game_initialize` calls `transport_initialize` (0x82130), and `game_dispose` calls `transport_dispose` (0x822d0) (`src/halo/game/game.c:119`). Three helper functions, `transport_get_nonce`, `transport_get_key`, and `transport_get_xnaddr`, support Xbox secure-address authentication.

## Layer 2 — connection (`network_connection.c`, 18/18 ported)

The `network_connection` struct is 0x38 bytes (`src/halo/networking/network_connection.h`). It holds a reliable endpoint with a reliable circular queue, an unreliable endpoint with an unreliable queue, flags, and counters. The server variant, `network_server_connection`, is 0x50 bytes. It adds an endpoint set, four child clients, and an accept gate.

- `network_connection_new(flags, port)` (0x1296b0) creates a TCP reliable endpoint, a UDP unreliable endpoint, and their queues. Flag value 1 selects server mode (listen-set limit 5, port `0x141e` = 5150). Flag value 2 selects client mode (port `0x141f` = 5151).
- `network_connection_connect` (0x128460) runs on the client, asynchronously. `network_connection_server_accept_client_connection` (0x1285c0) runs on the server.
- `network_connection_write` (0x128e00) picks a send path by connection type. A server connection sends through the datagram function `FUN_00084740`. A reliable connection loops `send_endpoint` calls, stopping on return value `-4`. An unreliable connection sends through `FUN_00084740` or a best-effort send.
- `network_connection_idle` (0x129a30) runs on the server. It calls `poll_endpoint_set`, rewinds the set, then handles each ready endpoint. For a new client, it calls `FUN_00084450` and `network_connection_new_serverside_client` (0x129270). For an existing client, it calls `network_connection_idle_client_reliable_endpoint` (0x1294d0), which receives data into a 0x8000-byte queue.
- The read functions are `network_connection_read_reliable` (0x1292f0) and `network_connection_read_unreliable` (0x1286e0).

## Layer 3 — message frame + crypto (`message_header.c`)

The wire header is a 16-bit word. Bits 4-15 hold the size (`size = header >> 4`). Bits 0-1 hold flags. Bits 2-3 hold the category (see `GET_MESSAGE_SIZE` and the category mask in the handlers). `build_message_header` (0x80b40) builds this header. `create_message` (0x80ca0) wraps a payload with the header. `byte_swap_message_header` (0x80c20) converts between host and network byte order.

Cryptography functions include `tea_encrypt`/`tea_decrypt` (the TEA cipher), `key_message_xor_keystream`, `message_encrypt`/`message_decrypt` (0x80940/0x80a40), and prime and RSA helpers starting at `FUN_00080eb0`. The key-agreement functions in `key_agreement.c` (0x805a0/0x80620) are unlifted. System link disables encryption. The assert text confirms this: "encryption should not be active".

## Layer 4 — packet serialization (`network_messages.c` + `data_packet_groups.c`)

- `data_packet_groups.c` defines field descriptors as five `short` values, ending with a type-9 terminator. It contains `encode_packet_group` (0x11aca0) and `compute_packet_field_sizes` (0x11add0).
- `network_messages.c` contains decode-state helpers, `verify_packet_group_definitions` (0x11a930), `decode_packet_group` (0x11aa40), a hash table, and an LRA cache. `initialize_network_game_packets` (0x12b640) validates `s_network_game_messages_group` (0x323510).
- `encode_network_game_message` (0x12b700, at `network_messages.c:1400`) checks `message_struct_size` for each message type. The source defines sizes for message types 0 through 34, for example `server_game_advertise` (0x114), `server_game_settings_update` (0x434), `server_game_update` (0x210), and `client_game_update` (0x88). It wraps the message with `create_message`.

## Layer 5 — game protocol / state machines

Key globals: the client pointer at `0x46e8c0`, the server pointer at `0x46e8bc`, the abort flag at `0x46e8c6`, and the keepalive timestamp at `0x46e8c8`. The server static block sits at `0x5a90e0`. The client static block sits at `0x5a95a0`. Two functions in `network_game_globals.c`, `network_game_set_number_of_games_played` and `network_game_set_random_seed`, reuse a local variable named `server` to hold the client pointer. This is a naming inconsistency in the source, not a logic error.

### Client
`FUN_00127070` (0x127070) dispatches on the state field at `client+0xca6`:

| State | Handler | Role |
|---|---|---|
| 0 searching | `network_game_client_idle_searching` 0x1268a0 | broadcast game search + ping |
| 1 joining | `FUN_00126b60` | TCP connect, send join request |
| 2 pregame | `FUN_00126ce0` | keepalive, settings |
| 3 ingame | `FUN_00126db0` | stale detection, game updates |
| 4 postgame | `network_game_client_idle` 0x126f40 | keepalive/reconnect |

For incoming messages, `FUN_001260c0` (0x1260c0) drains the queue through `FUN_001298f0`, then `FUN_00127ea0` (0x127ea0) switches on message type.

### Server
`network_game_server_start` (0x12eb20, deactivated) runs the server tick in this order:
1. `network_connection_idle` accepts new connections.
2. `FUN_0012d880` (deactivated) adds a new client.
3. `FUN_0012d9f0` handles public-endpoint datagrams.
4. `FUN_0012e580` (deactivated) handles client machines.
5. The tick dispatches on the state field at `server+4`: state 0 (pregame) calls `FUN_0012e750` (deactivated), state 2 (postgame) calls `FUN_0012db60`. State 1 (ingame) has no separate handler listed here.

Datagrams route through `FUN_00130270` (0x130270). Connected messages route through `FUN_00130580` (0x130580). The broadcast helper `FUN_0012f430` (0x12f430) loops over the four machine slots.

Server states: 0 pregame, 1 ingame, 2 postgame.

### Message types
Source: `network_client_message_handler.c:462` and `network_server_message_handler.c:432`.

Client → server:

| Opcode | Name | Notes |
|---|---|---|
| `0x00` | broadcast_game_search | UDP |
| `0x01` | ping | UDP |
| `0x0c` | join_request | |
| `0x0d` | add_player_pregame | |
| `0x0e` | remove_player_pregame | |
| `0x0f` | settings | |
| `0x10` | player_settings | |
| `0x11` | game_start | |
| `0x12` | graceful_exit_pregame | |
| `0x13` | message_client_map_is_precached_pregame | The server handler's log string for this case reuses the `0x12` text. This looks like a decompile artifact and is unresolved. |
| `0x18` | loaded | |
| `0x19` | client_game_update | UDP, every 16 ms |
| `0x1a` | add_player_ingame | |
| `0x1b` | remove_player_ingame | |
| `0x1c` | host_crashed_cry_for_help | |
| `0x1d` | join_new_host | |
| `0x20` | remove_player_postgame | |
| `0x21` | switch_to_pregame | |
| `0x22` | graceful_exit_postgame | |

Server → client:

| Opcode | Name | Notes |
|---|---|---|
| `0x02` | game_advertise | |
| `0x03` | pong | |
| `0x04` | machine_accepted | |
| `0x05` | machine_rejected | |
| `0x06` | game_settings_update | size 0x434 |
| `0x07` | pregame_countdown | |
| `0x08` | begin_game | |
| `0x09` | graceful_exit_pregame | |
| `0x0a` | pregame_keep_alive | |
| `0x0b` | postgame_keep_alive | |
| `0x14` | game_update | |
| `0x15` | add_player_ingame | |
| `0x16` | remove_player_ingame | |
| `0x17` | game_over | |
| `0x1e` | switch_to_pregame | |
| `0x1f` | graceful_exit_postgame | |

### State sync (`network_game_manager.c`, 20/20 ported)

The `network_game` blob starts at `game+8` and is 0x434 bytes. It holds four machine records (offset `+0x114`, stride 0x44), sixteen player records (offset `+0x226`, stride 0x20), a random seed, and a games-played counter. Functions exist to add, update, and remove machines and players, plus `network_game_spawn_player` and `network_game_reset_for_next_round`. Serialization uses two message types: `message_server_game_settings_update` (0x434 bytes) and `message_server_game_update` (0x210 bytes).

### Determinism / lockstep (`game_time.c:257`)

`game_time_update` handles the case `game_connection() == 2` (server). It calls `network_game_server_get_oldest_client_update_received` to gate `maximum_ticks` against `game_time_get()`. If the server gets more than 0x80 ticks ahead of a client, it asserts: "update server is too far ahead of a client for the client to ever catch up!". `network_game_server_stalled_on_client` toggles the stall state. On the client, state 1 clamps `maximum_ticks` to `update_get_maximum_actions()`. `network_game_server_update_ticks` applies the tick count. This logic is the part most sensitive to desync.

## Join / session trace (host + client)

```
CLIENT idle_searching (0x1268a0)
  encode 0x00 broadcast_game_search -> network_connection_write(UDP, 255.255.255.255:0x141e)
HOST   network_game_server_start -> FUN_0012d9f0 -> FUN_00130270 datagram case 0
        -> network_game_server_reset_to_pregame [deact]  (builds 0x114 advertise)
CLIENT FUN_00127260 (0x02 advertise) -> FUN_00125ce0 advertised-games list -> UI
CLIENT initiate_join_game -> network_connection_connect (TCP :0x141e)
HOST   network_connection_idle accept -> network_connection_new_serverside_client
        -> FUN_0012d880 add client [deact]
CLIENT send 0x0c join_game_request (FUN_00126b60)
HOST   FUN_00130580 case 0xc -> FUN_0012f990 join handler [deact]
        -> network_game_server_accept_client_machine_into_game
HOST   broadcast 0x04 machine_accepted -> 0x06 game_settings_update (FUN_0012f5d0)
CLIENT 0x04 -> pregame state 2; HOST state 0 (FUN_0012e750 loop)
... countdown 0x07 / begin_game 0x08 -> state 3 ...
INGAME: CLIENT end_frame -> 0x19 client_game_update (UDP) every 16ms
        HOST FUN_00130270 case 0x19 -> handle_client_update_packet
        HOST broadcast 0x14 server_game_update (reliable) -> CLIENT FUN_00127a50
```

## Practical notes

- `docs/system-link-rng-desync.md` is the authoritative doc for the open RNG-desync issue. RNG divergence is a downstream symptom. Collision, matrix, and float-order drift are the upstream causes under investigation. Session ds94 implicated `FUN_001a2f40` (now `ported:false`). Bisection is incomplete.
- `docs/networking_system_link_bug.md` proposes a top hypothesis: the pregame branch in `game_time.c` calls `0x12e1d0(server, false)` every frame. `0x12e1d0` is itself an unported thunk.
- In the debug binary, transport error codes map to strings at `0x81c80` and to an enum table at `0x266170`.
- kb.json groups at least two unrelated clusters under networking object names. `0x124730` is a model/marker function grouped under `network_client_manager.obj`. A cluster of about 15 functions (`0x131a20` through `0x132ea0`, including `flag_render_proper`) is a CTF flag-widget cluster labeled `telnet_console`. Neither cluster belongs to system link. Both are candidates for a kb.json object-reassignment pass.
