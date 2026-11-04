// SDK34 SupplicantStateChangeData -> SDK33 onStateChanged callback.
// Inserted only into the exact Bp callback method body. Original PAC prologue,
// saved registers, stack canary, CFI address and unwind epilogue are retained.
// x20 = StateChangeData, x21 = this, x19 = ScopedAStatus sret, x24 = TLS.
// Android14 generated readFromParcel confirms offsets: state0,id4,ssid8,
// fixed BSSID32[6],keyMgmt40,frequency44,fils48. Legacy callback wire code24.
.text
.arch armv8.5-a
.global ace3v_wifi_state_bridge
.type ace3v_wifi_state_bridge,%function
ace3v_wifi_state_bridge:
    // Non-owning vector<uint8_t> ABI view over fixed BSSID[6]. The old method
    // synchronously serializes this const reference and never owns/frees it.
    // SP[0..23] are scratch; SP[24..31] is the original stack canary.
    add x9, x20, #32
    add x10, x9, #6
    stp x9, x10, [sp]
    str x10, [sp, #16]
    mov x8, x19
    mov x0, x21
    ldr w1, [x20]
    mov x2, sp
    ldr w3, [x20, #4]
    add x4, x20, #8
    ldrb w5, [x20, #48]
    bl legacy_callback
    b original_epilogue
.size ace3v_wifi_state_bridge,.-ace3v_wifi_state_bridge
