<include>
  <!-- Plusnet-Trunk der Praxis. Wird von telefon/freeswitch/einrichten.sh nach
       $FS_ETC/sip_profiles/external/ kopiert, das Passwort kommt dabei aus .env.
       Eingehende Anrufe landen im Dialplan-Kontext "public". -->
  <gateway name="plusnet">
    <param name="username" value="@@SIP_USER@@"/>
    <param name="password" value="@@SIP_PASS@@"/>
    <param name="realm" value="@@SIP_DOMAIN@@"/>
    <param name="from-domain" value="@@SIP_DOMAIN@@"/>
    <!-- Wichtig: sip.plusnet.de löst auf acht IPs auf. Holt FreeSWITCH die
         Nonce beim einen Server und schickt die authentifizierte Anfrage an
         einen anderen, kennt der die Nonce nicht -> 401 in Dauerschleife. Ein
         einzelner voiceXX-Host zeigt auf genau eine IP und beendet das.
         Realm und From-Domain bleiben sip.plusnet.de, sonst passt die Auth nicht. -->
    <param name="proxy" value="@@SIP_PROXY@@"/>
    <param name="register-proxy" value="@@SIP_PROXY@@"/>
    <param name="register" value="true"/>
    <param name="register-transport" value="udp"/>
    <param name="expire-seconds" value="1800"/>
    <param name="retry-seconds" value="30"/>
    <param name="ping" value="30"/>
    <param name="context" value="public"/>
    <param name="caller-id-in-from" value="false"/>
  </gateway>
</include>
