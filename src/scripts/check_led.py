from gpiozero import LED
from time import sleep

led = LED(18)
for i in range(5):
    led.on();  print("ON");  sleep(1)
    led.off(); print("OFF"); sleep(1)
