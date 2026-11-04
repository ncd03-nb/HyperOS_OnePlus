
.method private static ace3vSupplicantCommand(Lcom/android/server/wifi/SupplicantStaIfaceHal;Ljava/lang/String;)Landroid/util/Pair;
    .registers 5
    :try_start_ace3v
    invoke-virtual {p0, p1}, Lcom/android/server/wifi/SupplicantStaIfaceHal;->doSupplicantCommand(Ljava/lang/String;)Landroid/util/Pair;
    move-result-object v0
    :try_end_ace3v
    .catch Landroid/os/ServiceSpecificException; {:try_start_ace3v .. :try_end_ace3v} :catch_ace3v
    return-object v0
    :catch_ace3v
    move-exception v0
    const-string v1, "MiuiWifiService"
    const-string v2, "Optional Xiaomi supplicant command rejected by HAL"
    invoke-static {v1, v2, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I
    sget-object v0, Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;
    const-string v1, ""
    invoke-static {v0, v1}, Landroid/util/Pair;->create(Ljava/lang/Object;Ljava/lang/Object;)Landroid/util/Pair;
    move-result-object v0
    return-object v0
.end method
