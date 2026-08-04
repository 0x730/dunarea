# Date GRDC locale

Monitorul compară valoarea curentă GloFAS de la intrarea în deltă numai cu
seria zilnică măsurată pentru **Ceatal Izmail — GRDC 6742900**.

În portalul GRDC, selectează stația `6742900`, seria **daily discharge** și
formatul **GRDC Export (ASCII text)**. Pune fișierul `6742900_Q_Day.Cmd.txt`
în acest director, fără să-i modifici conținutul. Importatorul păstrează în API
identificatorul stației, intervalul, data ultimei actualizări și SHA-256-ul
fișierului brut.

Un pachet de control mai larg poate include și stații de pe cursul principal
amonte, dar ele nu sunt amestecate cu comparația Ceatal. Catalogul curent trebuie
folosit pentru a confirma identificatorii și intervalele înaintea cererii.

Condițiile portalului GRDC permit utilizarea datelor brute pentru cercetare
necomercială și interzic redistribuirea lor către terți sau pe internet. Din
acest motiv exporturile rămân locale; aplicația publică doar statistici derivate
și atribuie sursa: „The Global Runoff Data Centre, 56068 Koblenz, Germany”.

Surse:

- https://grdc.bafg.de/data/data_portal/
- https://grdc.bafg.de/data/data_portal_guide/
