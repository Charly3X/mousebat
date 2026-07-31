# battary

Трей-индикатор заряда беспроводной мыши Logitech для KDE Plasma.

Ядро не создаёт `power_supply` для мышей на ресивере Logi Bolt, поэтому штатный
виджет «Батарея и яркость» их не показывает. `battary` читает заряд сам — по HID++ 2.0
через `/dev/hidraw` ресивера — и рисует иконку в трее.

Только чтение: никаких настроек в мышь не записывается, поэтому конфигурация
[logiops](https://github.com/PixlOne/logiops) (`/etc/logid.cfg`) остаётся нетронутой.
С работающим `logid` соседствует корректно — свои ответы отличаются по `software_id`.

## Что показывает

- Иконка батарейки с заливкой по уровню заряда; жёлтая ниже 20%, красная ниже 10%.
- При зарядке — молния поверх заливки.
- Тултип: имя устройства и `73% — разряжается`.
- Потеря связи (мышь уснула, ресивер выдернут) — иконка блёкнет, тултип «нет связи».
  Возврат подхватывается сам.
- Меню правой кнопкой: «Обновить», «Выход».

Опрос раз в 5 минут; при потерянной связи — раз в минуту.

Мышь не зашита в код: ищется первое указывающее устройство на любом Logitech-ресивере.

## Установка

```sh
sudo apt install python3-pyqt6

sudo cp packaging/42-battary-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw

mkdir -p ~/.config/systemd/user
cp packaging/battary.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now battary.service
```

udev-правило помечает hidraw-узлы Logitech тегом `uaccess` — доступ получает
пользователь активной локальной сессии, без групп и без root-демона.

Юнит рассчитан на расположение проекта в `~/projects/battary`; при другом пути
поправь `WorkingDirectory` и `PYTHONPATH` в `packaging/battary.service`.

Логи: `journalctl --user -u battary -f`

## Диагностика

```sh
python3 tools/spike_probe.py
```

Печатает все найденные HID++-узлы, ответы по индексам 1–6, имена и типы устройств,
индексы батарейных фич и сырые байты ответа о заряде. Первое, что стоит запустить,
если иконка показывает «нет связи».

Контрольный лист состояний иконки:

```sh
QT_QPA_PLATFORM=offscreen python3 tools/preview_icon.py /tmp/preview.png
```

## Тесты

```sh
python3 -m pytest
```

Железо не требуется: транспорт подменяется записанными байтами, `/sys` — временным
каталогом. Тесты иконки пропускаются, если не установлен PyQt6.

## Устройство

| Модуль | Задача |
|---|---|
| `battary/hidpp.py` | пакеты HID++, фильтрация чужих ответов, обе схемы ошибок |
| `battary/discovery.py` | поиск HID++-узлов и мышей за ними |
| `battary/battery.py` | заряд через фичу `0x1004`, fallback на `0x1000` |
| `battary/icon.py` | отрисовка иконки |
| `battary/tray.py` | иконка, меню, опрос в рабочем потоке |

Опрос вынесен в отдельный поток: при потерянной связи перебор ресиверов и индексов
занимает секунды, и в главном потоке это подвесило бы интерфейс.

Дизайн: [`docs/superpowers/specs/2026-07-31-mouse-battery-tray-design.md`](docs/superpowers/specs/2026-07-31-mouse-battery-tray-design.md)
