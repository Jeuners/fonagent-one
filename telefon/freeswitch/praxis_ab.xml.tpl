<include>
  <!-- Anrufbeantworter der Praxis: annehmen, Ansage, aufnehmen, auflegen.
       Die Aufnahme landet im Eingang der Pipe; der Dateiname traegt Zeit und
       Rufnummer (CLIP), damit pipe.watch beides uebernehmen kann. -->
  <extension name="praxis_anrufbeantworter">
    <condition field="destination_number" expression="^.*$">
      <action application="set" data="record_stereo=false"/>
      <action application="set" data="RECORD_ANSWER_REQ=true"/>
      <action application="answer"/>
      <!-- kurze Pause, sonst schneidet die Gegenstelle den Anfang der Ansage ab -->
      <action application="sleep" data="700"/>
      <action application="playback" data="@@PROJEKT@@/telefon/ansage.wav"/>
      <!-- record: <datei> <max_sekunden> <stille_schwelle> <stille_sekunden> -->
      <action application="record"
              data="@@PROJEKT@@/telefon/eingang/${strftime(%Y%m%d-%H%M%S)}_${caller_id_number}.wav 120 200 4"/>
      <action application="hangup"/>
    </condition>
  </extension>
</include>
