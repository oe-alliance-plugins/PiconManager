from gettext import bindtextdomain, dgettext, gettext

from Components.Language import language
from Tools.Directories import resolveFilename, SCOPE_PLUGINS

PluginLanguageDomain = "PiconManager"
PluginLanguagePath = "Extensions/PiconManager/locale"


def localeInit():
	bindtextdomain(PluginLanguageDomain, resolveFilename(SCOPE_PLUGINS, PluginLanguagePath))


def _(text):
	# dgettext returns the text itself when the plugin has no translation, fall back to enigma2's then
	if (translated := dgettext(PluginLanguageDomain, text)) != text:
		return translated
	return gettext(text)


localeInit()
language.addCallback(localeInit)


DEFAULT_PICON_PATH = '/usr/share/enigma2/picon'
PICON_PATHS = [  # drives RED steps through, after the last one the folder selection opens
	'/usr/share/enigma2/picon',
	'/media/usb/picon',
	'/media/hdd/picon',
	'/picon',
	'/data/picon',
	'/media/mmc/picon',
	'/media/sdcard/picon',
	'/media/hdd/XPicons/picon',
	'/media/hdd/ZZPicons/picon',
	'/media/usb/XPicons/picon',
	'/media/usb/ZZPicons/picon',
	'/usr/share/enigma2/XPicons/picon',
	'/usr/share/enigma2/ZZPicons/picon'
]


__version__ = "2.7.1"
