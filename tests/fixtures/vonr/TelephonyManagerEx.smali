.class public Lmiui/telephony/TelephonyManagerEx;
.super Ljava/lang/Object;

.method public getMiuiTelephony()Lmiui/telephony/IMiuiTelephony;
    .registers 2
    const/4 v0, 0x0
    return-object v0
.end method

.method public isVoNREnabled(I)Z
    .registers 6
    const/4 v0, 0x0
    :try_start
    invoke-virtual {p0}, Lmiui/telephony/TelephonyManagerEx;->getMiuiTelephony()Lmiui/telephony/IMiuiTelephony;
    move-result-object v1
    if-eqz v1, :done
    invoke-interface {v1, p1}, Lmiui/telephony/IMiuiTelephony;->isVoNREnabled(I)Z
    move-result v0
    :try_end
    :done
    return v0
    :catch
    move-exception v1
    return v0
    .catch Ljava/lang/Exception; {:try_start .. :try_end} :catch
.end method
