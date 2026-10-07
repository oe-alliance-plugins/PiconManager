#!/usr/bin/env python
# -*- coding: utf-8 -*-
#######################################################################
# maintainer: einfall & schomi (schomi@vuplus-support.org)
# This plugin is free software, you are allowed to
# modify it (if you keep the license),
# but you are not allowed to distribute/publish
# it without source code (this version and your modifications).
# This means you also have to distribute
# source code of your modifications.
#######################################################################
# this version is complete modified by shadowrider and NaseDC
# python3 fix by jbleyel
#######################################################################
#  Thanks to vuplus-support.org for the webspace
#######################################################################
# 20250328 recode by @lululla
# all fix:
# progressbar on download
# skin fixed
# downloaded request fixed
# counter
# show picons on Ok
# set and save piconpath
# add remove picons unused
# setScale(1) on show picon preview
# Case-insensitive checking
# Symbollink handling
# Special character validation
# ##########################

# Built-in
from contextlib import suppress
from datetime import date
from os import access, listdir, makedirs, remove, replace, statvfs, W_OK
from os.path import dirname, exists, isdir, islink, ismount, join, realpath
from random import choice
from re import search, sub
from shutil import rmtree
from unicodedata import normalize
from urllib.parse import quote
from threading import get_ident, local
from time import monotonic
from uuid import uuid4

from twisted.internet import defer, threads
import requests

# Enigma2
from enigma import (
	eListboxPythonMultiContent, eServiceCenter, eServiceReference, getDesktop, gFont,
	RT_HALIGN_LEFT, RT_VALIGN_CENTER
)
from Screens.ChannelSelection import SimpleChannelSelection, service_types_radio, service_types_tv
from Screens.HelpMenu import HelpableScreen
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from Components.ActionMap import ActionMap, HelpableActionMap
from Components.ConfigList import ConfigListScreen
from Components.config import (
	ConfigInteger, ConfigSelection, ConfigSubsection, ConfigText,
	ConfigYesNo, config, getConfigListEntry
)
from Components.FileList import FileList
from Components.Label import Label
from Components.MenuList import MenuList
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Plugins.Plugin import PluginDescriptor
from Tools.Directories import resolveFilename, SCOPE_PLUGINS

# Local/project-specific
from ServiceReference import ServiceReference
from skin import parameters

from . import _, __version__, PICON_PATHS, DEFAULT_PICON_PATH
from .piconnames import correctedFileName, getInteroperableNames, interoperableName, reducedName, VTiName  # check for by-name-picons that dont fit with VTi Syntax (Picon Buddy Mode)


# constants
pname = _("PiconManager")
pdesc = _("Manage your Picons")
pversion = __version__

picon_tmp_dir = "/tmp/piconmanager/"
picon_debug_file = "/tmp/piconmanager_error"
picon_notfound_file = "/tmp/picon_dl_err"
picon_info_file = "picons/picon_info.txt"
picon_list_file = "zz_picon_list.txt"
USER_DEFINED = "user_defined"  # old savetopath value of a folder chosen by the user
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PROTECTED_PICONS = ("picon_default.png",)
MODE_REF, MODE_NAME, MODE_SNP = "ref", "name", "snp"

# http: the TLS handshake of the server often hangs ~10 s and gets reset (checked 2026-10), http answers at once
server_choices = [("http://picons.vuplus-support.org/", "VTi: vuplus-support.org"), ]
agents = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.10 Safari/605.1.1'}

# config declare
config.plugins.piconmanager = ConfigSubsection()
config.plugins.piconmanager.alter = ConfigInteger(default=0, limits=(0, 1000))
config.plugins.piconmanager.debug = ConfigYesNo(default=False)
config.plugins.piconmanager.savetopath = ConfigText(default=DEFAULT_PICON_PATH, fixed_size=False)
config.plugins.piconmanager.saving = ConfigYesNo(default=True)
config.plugins.piconmanager.selected = ConfigText(default="All", fixed_size=False)
config.plugins.piconmanager.server = ConfigSelection(default=server_choices[0][0], choices=server_choices)
config.plugins.piconmanager.spicon = ConfigText(default="", fixed_size=False)
config.plugins.piconmanager.snpsave = ConfigSelection(default="ref", choices=[("ref", _("Service reference (all images)")), ("snp", _("Service name (SNP)"))])

# the skins below are designed for 1280x720 and get scaled to the real desktop size
SCALE = getDesktop(0).size().width() / 1280.0
LIST_ITEM_HEIGHT = int(30 * SCALE)
LIST_FONT_SIZE = int(22 * SCALE)
PLUGIN_PIC = resolveFilename(SCOPE_PLUGINS, "Extensions/PiconManager/pic/")


def scaleSkin(skin):
	skin = skin.replace("{pic}", PLUGIN_PIC)
	if abs(SCALE - 1.0) < 0.01:
		return skin

	def scaleNumbers(match):
		values = (str(round(int(x) * SCALE)) if x.strip().isdigit() else x for x in match.group(2).split(","))
		return f'{match.group(1)}{",".join(values)}"'

	skin = sub(r'((?:position|size|itemHeight)=")([^"]+)"', scaleNumbers, skin)
	return sub(r'(font="[^;"]+;)(\d+)"', scaleNumbers, skin)


def ensure_str(s):
	if isinstance(s, bytes):
		return s.decode("utf-8", errors="replace")
	return s


def ListEntry(entry):
	x, y, w, h = parameters.get("PiconManagerList", (int(5 * SCALE), 0, int(1120 * SCALE), LIST_ITEM_HEIGHT))
	return [entry, (eListboxPythonMultiContent.TYPE_TEXT, x, y, w, h, 0, RT_HALIGN_LEFT | RT_VALIGN_CENTER, entry[0])]


def createPiconMenuList():
	menuList = MenuList([], enableWrapAround=True, content=eListboxPythonMultiContent)
	font, size = parameters.get("PiconManagerListFont", ('Regular', LIST_FONT_SIZE))
	menuList.l.setFont(0, gFont(font, size))
	menuList.l.setItemHeight(LIST_ITEM_HEIGHT)
	return menuList


def showPiconPixmap(widget, path):
	"""Show the picon file path in the Pixmap widget, hide the widget when that fails."""
	if path and exists(path):
		try:
			widget.instance.setPixmapFromFile(path)
			widget.instance.setScale(1)
			widget.show()
			return
		except Exception as e:
			print(f"[PiconManager] Error loading picon {path}: {str(e)}")
			errorWrite(f"Failed to load {path}: {str(e)}")
	widget.hide()


def debugWrite(path, text, mode="a"):
	"""Write text to a debug file, only with debug logging switched on."""
	if not config.plugins.piconmanager.debug.value:
		return
	try:
		with open(path, mode, encoding="utf-8", errors="replace") as f:
			f.write(f"{text}\n")
	except OSError as e:
		print(f"[PiconManager] Error writing debug log {path}: {str(e)}")


def errorWrite(error):
	debugWrite(picon_debug_file, error)


def notfoundWrite(picon, mode="a"):
	debugWrite(picon_notfound_file, picon, mode)


_threadData = local()


def httpSession():
	"""The requests session of the current thread: keeps the connection to the picon server open between
	the picons (a new connection per picon is several times slower)."""
	session = getattr(_threadData, "session", None)
	if session is None:
		session = _threadData.session = requests.Session()
		session.headers.update(agents)
	return session


def fetchData(url):
	"""Download url and return the content, raises on errors (runs in a thread)."""
	response = httpSession().get(url, timeout=(3.05, 10))
	response.raise_for_status()
	return response.content


def fetchFile(url, path, png=False):
	"""Download url to path (runs in a thread).

	The data goes to a temporary file first, so a failed or aborted download never leaves a broken
	picon behind and an existing symlink gets replaced instead of overwriting the shared target.
	With png=True everything that is not a PNG (error pages ...) is rejected. Returns True/False.
	"""
	tmp = f"{path}.{get_ident()}.part"  # per thread: two downloads of the same file never share it
	try:
		with httpSession().get(url, stream=True, timeout=(10, 30)) as response:
			if response.status_code != 200:
				print(f"[PiconManager] HTTP {response.status_code}: {url}")
				return False
			size = 0
			with open(tmp, "wb") as f:
				for chunk in response.iter_content(chunk_size=8192):
					if not chunk:
						continue
					if png and size == 0 and not chunk.startswith(PNG_MAGIC):
						print(f"[PiconManager] Not a PNG: {url}")
						break
					f.write(chunk)
					size += len(chunk)
		if size:
			replace(tmp, path)
			return True
	except Exception as e:
		print(f"[PiconManager] Download failed: {url} {str(e)}")
	with suppress(OSError):
		remove(tmp)
	return False


def piconFields(serviceref):
	"""The first 10 fields of a service reference (the picon file name parts) or None."""
	fields = str(serviceref).split(":", 10)[:10]
	if len(fields) < 10:
		return None
	return fields


def piconRefName(serviceref):
	"""The service reference picon name with reftype 1 like the picon servers use it (IPTV 4097/5001/... too)."""
	fields = piconFields(serviceref)
	if not fields:
		return ""
	fields[0] = "1"
	return "_".join(fields)


def piconRefNames(serviceref):
	"""All service reference names the picon renderers try for a service (see Components/Renderer/Picon.py)."""
	fields = piconFields(serviceref)
	if not fields:
		return []
	names = ["_".join(fields)]
	if not fields[6].endswith("0000"):
		fields[6] = fields[6][:-4] + "0000"  # namespace without sub-network
		names.append("_".join(fields))
	if fields[0] != "1":
		fields[0] = "1"
		names.append("_".join(fields))
	if fields[2] != "1":
		fields[2] = "1"
		names.append("_".join(fields))
	return names


def cleanServiceName(name):
	return (name or "").replace('\x80', '').replace('\x86', '').replace('\x87', '')


def _snpNames(name):
	name = cleanServiceName(name)
	if not name or "SID 0x" in name or name == "<n/a>":
		return "", ""
	utf8Name = normalize("NFKD", "".join(c for c in name if c not in '\\/:*?"<>|' and ord(c) > 31)).strip().rstrip(". ").lower()  # like sanitizeFilename
	legacyName = sub("[^a-z0-9]", "", utf8Name.replace("&", "and").replace("+", "plus").replace("*", "star"))
	return utf8Name, legacyName


def stripQuality(name):
	return sub(r"(fhd|uhd|hd|sd|4k)$", "", name).strip()


def piconSnpNames(name):
	"""Service name picon names (utf8 / legacy SNP names) in the order the picon renderers try them."""
	utf8Name, legacyName = _snpNames(name)
	names = [utf8Name, legacyName, stripQuality(utf8Name), stripQuality(legacyName)]
	names += [normalize("NFC", utf8Name), normalize("NFC", stripQuality(utf8Name))]  # utf8 names as files usually store them ("ü", not "u" + diaeresis)
	return list(dict.fromkeys(x for x in names if x))


def piconLegacyNames(name):
	"""The legacy SNP names (a-z0-9 only) of a service name, with and without HD/UHD/... suffix
	("ZDF HD": zdfhd, zdf - and zd like the picon renderer strips "fhd" from "zdfhd")."""
	utf8Name, legacyName = _snpNames(name)
	return list(dict.fromkeys(x for x in (legacyName, _snpNames(stripQuality(utf8Name))[1], stripQuality(legacyName)) if x))


def getServiceList(ref):
	try:
		root = eServiceReference(str(ref))
		serviceHandler = eServiceCenter.getInstance()
		if serviceHandler is None:
			print("[PiconManager] Error: Cannot get service handler instance")
			return []
		return serviceHandler.list(root).getContent("SN", True) or []
	except Exception as e:
		print(f"[PiconManager] Error getting service list: {str(e)}")
		return []


def getTVBouquets():
	return getServiceList(service_types_tv + ' FROM BOUQUET "bouquets.tv" ORDER BY bouquet')


def getRadioBouquets():
	return getServiceList(service_types_radio + ' FROM BOUQUET "bouquets.radio" ORDER BY bouquet')


def iterBouquetServices(ref, allAlternatives, depth=0):
	"""Yield (serviceref, servicename) of a bouquet: markers skipped, sub bouquets followed and
	alternatives resolved (the first service like the picon renderer does, or all of them)."""
	for serviceref, servicename in getServiceList(ref):
		if not serviceref:
			continue
		flags = eServiceReference(serviceref).flags
		if flags & eServiceReference.isMarker:
			continue
		if flags & eServiceReference.isGroup:
			members = [x for x in getServiceList(serviceref) if x and x[0]]
			for member in (members if allAlternatives else members[:1]):
				yield member[0], servicename or member[1]
		elif flags & eServiceReference.isDirectory:
			if depth < 3:
				yield from iterBouquetServices(serviceref, allAlternatives, depth + 1)
		elif servicename:
			yield serviceref, servicename


def buildChannellist(allAlternatives=False):
	channellist = []
	seen = set()
	try:
		allbouquets = getTVBouquets() + getRadioBouquets()
		print(f"[PiconManager] Found {len(allbouquets)} bouquets")
		for bouquet in allbouquets:
			if not bouquet or not bouquet[0]:
				continue
			for serviceref, servicename in iterBouquetServices(bouquet[0], allAlternatives):
				if serviceref not in seen:
					seen.add(serviceref)
					channellist.append((serviceref, servicename))
		print("[PiconManager] Built channel list with", len(channellist), "entries")
	except Exception as e:
		print(f"[PiconManager] Critical error building channel list: {str(e)}")
	return channellist


def detectSetMode(groupName, creator, folder):
	"""MODE_NAME for VTi by-name sets (group "by Name"), MODE_SNP for service name picon sets ("SNP" as a word
	in creator or folder), else MODE_REF."""
	if "by name" in groupName.lower():
		return MODE_NAME
	if search(r"(?<![a-z])snp(?![a-z])", f"{creator} {folder}".lower()):
		return MODE_SNP
	return MODE_REF


def uniqueIndex(pairs):
	"""{key: name} of (key, name) pairs, keys several names share are left out (ambiguous)."""
	index = {}
	ambiguous = set()
	for key, name in pairs:
		if not key or key in ambiguous:
			continue
		if index.get(key, name) != name:
			del index[key]
			ambiguous.add(key)
		else:
			index[key] = name
	return index


def piconSetMode(item):
	"""The mode of a picon list entry (MODE_REF / MODE_NAME / MODE_SNP)."""
	return item[7]


def loadPiconNames(listUrl, list_file):
	"""Picon names (without .png) of a picon set from its zz_picon_list.txt, a cached copy in
	list_file first (runs in a thread)."""
	nameList = []
	reducedList = []
	try:
		if exists(list_file):
			with open(list_file, encoding="utf-8", errors="replace") as f:
				content = f.read()
		else:
			content = ensure_str(fetchData(listUrl))
		for line in content.splitlines():
			name = line.strip()
			if name.endswith('.png'):
				nameList.append(name[:-4])
				reducedList.append(reducedName(name[:-4]))
	except Exception as e:
		print(f"[PiconManager] Error loading picon list: {str(e)}")
	return nameList, reducedList


def screenHeader(name, title):
	return f'<screen name="{name}" title="{title}" position="center,center" size="1160,700" flags="wfNoBorder">'


class PiconManagerScreen(Screen, HelpableScreen):
	skin = scaleSkin(screenHeader("PiconManager", "PiconManager") + """
		<widget name="piconpath" position="20,10" size="190,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="left" />
		<widget name="piconpath2" position="210,10" size="500,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="piconspace" position="20,40" size="690,30" font="Regular;20" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="left" />
		<widget name="piconcount" position="20,70" size="690,30" font="Regular;20" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="left" />
		<widget name="picondownload" position="20,100" size="690,30" font="Regular;20" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="left" />
		<widget name="piconerror" position="20,130" size="690,30" font="Regular;20" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="left" />
		<widget name="piconslidername" position="816,254" size="240,30" font="Regular;20" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="center" />
		<widget name="selectedname" position="20,190" size="160,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="left" />
		<widget name="selected" position="180,190" size="210,30" noWrap="1" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="creatorname" position="20,220" size="160,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="left" />
		<widget name="creator" position="180,220" size="210,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="sizename" position="390,190" size="220,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="right" />
		<widget name="size" position="615,190" size="100,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="bitname" position="390,220" size="220,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="right" />
		<widget name="bit" position="615,220" size="100,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="spiconname" position="20,160" size="190,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="left" />
		<widget name="spicon" position="210,159" size="503,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="altername" position="20,255" size="590,30" font="Regular;20" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="right" />
		<widget name="alter" position="615,255" size="100,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="piconslider" position="740,286" size="400,10" borderWidth="1" borderColor="#00f8f2e6" foregroundColor="#00fba207" zPosition="5" />
		<widget name="picon" position="740,10" size="400,240" zPosition="3" transparent="1" borderWidth="0" borderColor="#0000000" alphatest="blend" />
		<widget name="list" position="10,315" size="1130,320" zPosition="3" foregroundColor="#00ffffff" foregroundColorSelected="#00fff000" scrollbarMode="showOnDemand" transparent="1" />
		<widget name="key_red" position="42,655" size="200,25" transparent="1" font="Regular;20" zPosition="3" />
		<widget name="key_green" position="265,655" size="200,25" transparent="1" font="Regular;20" zPosition="3" />
		<widget name="key_yellow" position="466,655" size="200,25" transparent="1" font="Regular;20" zPosition="3" />
		<widget name="key_blue" position="714,655" size="200,25" transparent="1" font="Regular;20" zPosition="3" />
		<ePixmap position="10,655" size="60,25" zPosition="3" pixmap="{pic}button_red.png" transparent="1" alphatest="on" />
		<ePixmap position="227,655" size="60,25" zPosition="3" pixmap="{pic}button_green.png" transparent="1" alphatest="on" />
		<ePixmap position="436,655" size="60,25" zPosition="3" pixmap="{pic}button_yellow.png" transparent="1" alphatest="on" />
		<ePixmap position="681,655" size="60,25" zPosition="3" pixmap="{pic}button_blue.png" transparent="1" alphatest="on" />
		<ePixmap position="916,650" size="60,35" zPosition="3" pixmap="{pic}button_info.png" transparent="1" alphatest="on" />
		<ePixmap position="977,650" size="60,35" zPosition="3" pixmap="{pic}button_menu.png" transparent="1" alphatest="on" />
		<ePixmap position="1038,650" size="60,35" zPosition="3" pixmap="{pic}button_channel.png" transparent="1" alphatest="on" />
		<ePixmap position="1095,650" size="60,35" zPosition="3" pixmap="{pic}button_help.png" transparent="1" alphatest="on" />
	</screen>""")

	def __init__(self, session):
		Screen.__init__(self, session)
		HelpableScreen.__init__(self)
		self.server_url = config.plugins.piconmanager.server.value
		self.picondir = config.plugins.piconmanager.savetopath.value.rstrip("/")
		if not self.picondir or self.picondir == USER_DEFINED:
			self.picondir = DEFAULT_PICON_PATH
		self.piconfolder = join(self.picondir, '')
		self.piconlist = []
		self.tried_mirrors = []
		self.group_list = []
		self.creator_list = []
		self.size_list = []
		self.bit_list = []
		self.picon_files = []
		self.prev_sel = None
		self.selectedSetId = ""
		self.selectedDirUrl = ""
		self.picon_list_file = ""
		self.downloadPiconPath = ""
		self.countload = 0
		self.counterrors = 0
		self.countskipped = 0
		self.total_downloads = 0
		self.lastProgressUpdate = 0.0
		self.cancelled = False
		self.progressDialog = None
		self.nameSet = set()
		self.fullNames = {}
		self.reducedNames = {}
		self.downloading = False
		self.closed = False
		self['piconpath'] = Label(_("Picon folder: "))
		self['piconpath2'] = Label(self.piconfolder)
		self['piconspace'] = Label("")
		self['piconcount'] = Label(_("Reading Channels..."))
		self['picondownload'] = Label(_("Picons loaded: "))
		self['piconerror'] = Label(_("Picons not found: "))
		self['piconslidername'] = Label()
		self['selectedname'] = Label(_("Show group: "))
		self['selected'] = Label()
		self['creatorname'] = Label(_("Creator: "))
		self['creator'] = Label()
		self['sizename'] = Label(_("Size: "))
		self['size'] = Label()
		self['bitname'] = Label(_("Color depth: "))
		self['bit'] = Label()
		self['altername'] = Label(_("Not older than X days: "))
		self['alter'] = Label(str(config.plugins.piconmanager.alter.value))
		self['spiconname'] = Label(_("Standard picon: "))
		self['spicon'] = Label()
		self.chlist = buildChannellist()
		self.getFreeSpace()
		self.countchlist = len(self.chlist)
		self.activityslider = ProgressBar()
		self["piconslider"] = self.activityslider
		self["piconslider"].hide()
		self['key_red'] = Label(_("Select drive"))
		self['key_green'] = Label(_("Download picons"))
		self['key_yellow'] = Label(_("Select path"))
		self['key_blue'] = Label(_("Remove Picons Unused"))
		self['picon'] = Pixmap()
		self["OkCancelActions"] = HelpableActionMap(
			self, "OkCancelActions",
			{
				"ok": (self.keyOK, _("Show random Picon")),
				"cancel": (self.keyCancel, _("Exit")),
			},
			-2
		)

		self["SetupActions"] = HelpableActionMap(
			self, "SetupActions",
			{
				"1": (self.sel_creator_back, _("Previous picon creator")),
				"3": (self.sel_creator_next, _("Next picon creator")),
				"4": (self.sel_size_back, _("Previous picon size")),
				"6": (self.sel_size_next, _("Next picon size")),
				"7": (self.sel_bit_back, _("Previous color depth")),
				"9": (self.sel_bit_next, _("Next color depth")),
			}
		)

		self["EPGSelectActions"] = HelpableActionMap(
			self, "EPGSelectActions",
			{
				"menu": (self.settings, _("More selections")),
				"nextService": (self.sel_satpos_next, _("Next Group")),
				"prevService": (self.sel_satpos_back, _("Previous Group")),
				"info": (self.set_picon, _("Set / clear standard Picon")),
				"red": (self.changeDrive, _("Select drive")),
				"timerAdd": (self.downloadPicons, _("Download picons")),
				"yellow": (self.keyYellow, _("Select path")),
				"blue": (self.showPiconRemover, _("Open picon remover")),
			},
			-2
		)
		self.channelMenuList = createPiconMenuList()
		self.setTitle(f"{pname}   {_('V')} {pversion}")
		self['list'] = self.channelMenuList
		self['list'].onSelectionChanged.append(self.showPic)
		self.keyLocked = True
		self.piconTempDir = picon_tmp_dir
		try:
			makedirs(self.piconTempDir, exist_ok=True)
		except OSError as e:
			print(f"[PiconManager] Error creating {self.piconTempDir}: {str(e)}")
		self.onLayoutFinish.append(self.getPiconList)
		self.onClose.append(self.__onClose)

	def __onClose(self):
		self.closed = True

	def selectedMediaFile(self, res):
		if res is not None:
			try:
				self.picondir = res.rstrip('/') or "/"
				self.piconfolder = join(self.picondir, '')
				makedirs(self.picondir, exist_ok=True)
				self['piconpath2'].setText(self.piconfolder)
				self.getFreeSpace()
				print(f"[PiconManager] New Path: {self.piconfolder}")
			except Exception as e:
				print(f"[PiconManager] Path selection failed: {str(e)}")
				self.session.open(
					MessageBox,
					_("Error selecting path:") + f"\n{str(e)}",
					MessageBox.TYPE_ERROR
				)

	def showPiconRemover(self):
		if self.downloading:
			return
		self.session.openWithCallback(
			self.afterRemoval,
			PicRemoverScreen,
			self.piconfolder
		)

	def afterRemoval(self, result=None):
		self.getFreeSpace()

	def settings(self):
		if self.piconlist and self.group_list and not self.keyLocked:
			self.session.openWithCallback(self.makeList, pm_conf)

	def set_picon(self):
		if self.keyLocked:
			return
		if config.plugins.piconmanager.spicon.value == "":
			self.session.openWithCallback(self.got_picon, SimpleChannelSelection, _("Select service for preferred picon"))
		else:
			self.got_picon()

	def got_picon(self, service=""):
		if service is None:  # channel selection cancelled
			return
		service_name = ""

		if isinstance(service, eServiceReference):
			service_name = ServiceReference(service).getServiceName()
			service2 = service.toString()
			for channel in self.chlist:
				if channel[0] == service2:
					service_name = channel[1]
					break
			config.plugins.piconmanager.spicon.value = f"{piconRefName(service2)}.png|{service_name}"
		else:
			config.plugins.piconmanager.spicon.value = ""

		config.plugins.piconmanager.spicon.save()
		self["spicon"].setText(service_name)
		with suppress(OSError):  # the cached previews show the old standard picon
			rmtree(self.piconTempDir)
			makedirs(self.piconTempDir, exist_ok=True)

		self.getPiconList()

	def sel_creator_next(self):
		self.change_filter_mode(+1, 0)

	def sel_creator_back(self):
		self.change_filter_mode(-1, 0)

	def sel_bit_next(self):
		self.change_filter_mode(+1, 1)

	def sel_bit_back(self):
		self.change_filter_mode(-1, 1)

	def sel_size_next(self):
		self.change_filter_mode(+1, 2)

	def sel_size_back(self):
		self.change_filter_mode(-1, 2)

	def sel_satpos_next(self):
		self.change_filter_mode(+1, 3)

	def sel_satpos_back(self):
		self.change_filter_mode(-1, 3)

	def change_filter_mode(self, direction: int, filter_type: int):
		# the filter config entries and lists exist once the picon list is loaded
		if self.keyLocked or not self.piconlist:
			return

		def formatValue(x):
			return _("All") if x == "All" else str(x)

		filter_configs = {
			0: ('creator', self.creator_list, 'creator', formatValue),
			1: ('bit', self.bit_list, 'bit', formatValue),
			2: ('size', self.size_list, 'size', formatValue),
			3: ('selected', self.group_list, 'selected', lambda x: formatValue(x).replace("+", " ").replace("-", " "))
		}
		config_name, current_list, widget, formatter = filter_configs[filter_type]
		if not current_list:
			return
		config_entry = getattr(config.plugins.piconmanager, config_name)

		try:
			idx = (current_list.index(config_entry.value) + direction) % len(current_list)
		except ValueError:
			idx = 0

		new_value = current_list[idx]
		config_entry.value = new_value
		self[widget].setText(formatter(new_value))
		if config.plugins.piconmanager.saving.value:
			config_entry.save()

		self.makeList(
			config.plugins.piconmanager.creator.value,
			config.plugins.piconmanager.size.value,
			config.plugins.piconmanager.bit.value,
			config.plugins.piconmanager.server.value,
			True,
			False,
			config.plugins.piconmanager.alter.value
		)

	def getFreeSpace(self):
		path = self.picondir
		while not exists(path):  # not created yet: show the space of the drive it will be created on
			parent = dirname(path)
			if parent == path:
				break
			path = parent
		if not access(path, W_OK):
			self['piconspace'].setText(_("No Write Permissions!"))
			return

		try:
			stat = statvfs(path)
			free_bytes = stat.f_frsize * stat.f_bavail

			if free_bytes >= 1024**3:  # 1 GB
				free_space = round(free_bytes / 1024**3, 1)
				unit = "GB"
			else:
				free_space = round(free_bytes / 1024**2, 1)
				unit = "MB"

			self['piconspace'].setText(
				_("FreeSpace:") + f" {free_space} {unit}"
			)

		except OSError as e:
			self['piconspace'].setText(
				_("FreeSpace: Error - {error}").format(error=str(e))
			)

	def showPic(self):
		self["picon"].hide()

		current_item = self['list'].getCurrent()
		if not self.piconlist or not current_item or len(current_item[0]) < 6:
			return

		sampleUrl = current_item[0][2]
		previewUrl = sampleUrl
		parts = config.plugins.piconmanager.spicon.value.split('|')
		if parts[0]:  # standard picon set: show it instead of the sample picon of the set
			mode = piconSetMode(current_item[0])
			if mode == MODE_NAME and len(parts) > 1:
				name = VTiName(parts[1])
			elif mode == MODE_SNP and len(parts) > 1:
				legacyNames = piconLegacyNames(parts[1])
				name = f"{legacyNames[0]}.png" if legacyNames else ""
			else:
				name = parts[0]
			if name:
				previewUrl = f"{current_item[0][1]}/{quote(name)}"

		self.downloadPiconPath = join(self.piconTempDir, f"{current_item[0][4]}.png")
		if exists(self.downloadPiconPath):
			self.showPiconFile(self.downloadPiconPath)
		else:
			self.downloadPreview(previewUrl, self.downloadPiconPath, sampleUrl if previewUrl != sampleUrl else None)

	def downloadPreview(self, url, path, fallbackUrl=None):
		"""Download and show a preview picon, fallbackUrl (the sample picon) when the set does not have url."""
		def done(ok):
			# the selection may have changed while downloading
			if self.closed or path != self.downloadPiconPath:
				return
			if ok:
				self.showPiconFile(path)
			elif fallbackUrl:
				self.downloadPreview(fallbackUrl, path)
			else:
				self.dataError(url)

		threads.deferToThread(fetchFile, url, path, True).addCallback(done)

	def getPiconList(self):
		print("[PiconManager] Started fetching picon list...")
		self.keyLocked = True

		self['piconcount'].setText(f"{_('Channels:')} {self.countchlist}")

		selected_text = str(config.plugins.piconmanager.selected.value)
		if selected_text == "All":
			formatted_text = _("All")
		else:
			formatted_text = selected_text.replace("_", ", ").replace("+", " ").replace("-", " ")
		self['selected'].setText(formatted_text)

		spicon_value = config.plugins.piconmanager.spicon.value
		if spicon_value:
			parts = spicon_value.split('|')
			display_text = parts[1] if len(parts) > 1 else parts[0]
		else:
			display_text = ""
		self['spicon'].setText(display_text)

		url = f"{self.server_url}{picon_info_file}"
		print(f"[PiconManager] Server: {self.server_url}")

		loading_entry = ListEntry((_("Loading, please wait..."),))
		self.channelMenuList.setList([loading_entry])

		d = threads.deferToThread(fetchData, url)
		d.addCallback(self.parsePiconList)
		d.addErrback(self.dataError2)

	def parsePiconList(self, data):
		if self.closed:
			return
		print("[PiconManager] Parsing picon list...")

		self.size_list = ["All"]
		self.bit_list = ["All"]
		self.creator_list = ["All"]
		self.piconlist = []
		self.group_list = ["All"]

		data = ensure_str(data).replace("\x86", "").replace("\x87", "")
		picon_data = [line for line in data.splitlines() if line and not line.startswith('<meta')]

		for picon_info in picon_data:
			info_list = picon_info.split(';')
			if len(info_list) < 9:
				continue

			# Extract picon info: folder;sample picon;date;name;group;creator;color depth;size;uploader
			dirUrl = self.server_url + quote(info_list[0].strip().strip("/"), safe="/")
			picUrl = f"{dirUrl}/{quote(info_list[1].strip())}"
			listUrl = f"{dirUrl}/{picon_list_file}"
			p_creator = info_list[5]
			p_bit = info_list[6].replace(' ', '').lower().replace('bit', ' bit')
			p_size = info_list[7].replace(' ', '').lower()
			p_pos = info_list[4]
			try:
				day, month, year = info_list[2].strip().split(".")
				p_date = date(int(year), int(month), int(day))
			except ValueError:
				p_date = None

			for item, target_list in [
				(p_size, self.size_list),
				(p_bit, self.bit_list),
				(p_creator, self.creator_list),
				(p_pos, self.group_list)
			]:
				if item not in target_list:
					target_list.append(item)

			p_name = f"{p_pos} | {p_creator} - {info_list[3]} | {p_size} | {p_bit} | {info_list[2]} | {info_list[8]}"
			# entry: (text, folder URL, sample picon URL, filter values, id, picon list URL, date, mode)
			self.piconlist.append((
				p_name, dirUrl, picUrl,
				(p_creator, p_size, p_bit, p_pos),
				str(uuid4()), listUrl, p_date, detectSetMode(p_pos, p_creator, info_list[0])
			))

		if not self.piconlist:
			self.dataError2(None)
			return

		for lst in [self.size_list, self.bit_list, self.creator_list, self.group_list]:
			lst[1:] = sorted(lst[1:])  # "All" stays first
		self.piconlist.sort(key=lambda x: x[0].lower())

		self._update_config_selections()

		self.keyLocked = False
		self.makeList(
			config.plugins.piconmanager.creator.value,
			config.plugins.piconmanager.size.value,
			config.plugins.piconmanager.bit.value,
			self.server_url,
			True,
			False,
			config.plugins.piconmanager.alter.value
		)

	def _update_config_selections(self):
		"""Update configuration selections while preserving current values."""
		if config.plugins.piconmanager.selected.value not in self.group_list:
			config.plugins.piconmanager.selected.setValue("All")
			self['selected'].setText(_("All"))

		# ConfigSubsection raises KeyError (not AttributeError) on some images, so hasattr() can not be used
		items = config.plugins.piconmanager.content.items
		for attr, source_list in (('bit', self.bit_list), ('size', self.size_list), ('creator', self.creator_list)):
			prev_value = items[attr].value if attr in items else None
			choices = [("All", _("All"))] + [(x, x) for x in source_list if x != "All"]
			# the new element gets its saved value from the settings file (ConfigSubsection.__setattr__)
			setattr(config.plugins.piconmanager, attr, ConfigSelection(default="All", choices=choices))
			if prev_value and prev_value in source_list:
				getattr(config.plugins.piconmanager, attr).value = prev_value

		for attr in ('creator', 'size', 'bit'):
			value = getattr(config.plugins.piconmanager, attr).value
			self[attr].setText(_("All") if value == "All" else str(value))

	def makeList(self, creator="All", size="All", bit="All", server=config.plugins.piconmanager.server.value, update=True, reload_picons=False, alter=0):
		"""
		Filter and display the picon list based on specified criteria.
		Args:
			creator: Filter by creator name ('All' for no filter)
			size: Filter by size ('All' for no filter)
			bit: Filter by bit depth ('All' for no filter)
			server: Server URL to use
			update: Whether to update the list display
			reload_picons: Whether to reload the picon list from server
			alter: Days threshold for filtering by date (0 = no date filter)
		"""
		if reload_picons:
			self.server_url = server
			self.channelMenuList.setList([])
			self.getPiconList()
			return

		if not update:
			return

		self['alter'].setText(str(alter))
		group = config.plugins.piconmanager.selected.value
		new_list = []
		today = date.today()

		for item in self.piconlist:
			if alter > 0:
				item_date = item[6]
				if item_date is None or (today - item_date).days > alter:
					continue

			p_creator, p_size, p_bit, p_group = item[3]
			if group not in ("All", p_group) or creator not in ("All", p_creator) or size not in ("All", p_size) or bit not in ("All", p_bit):
				continue

			new_list.append(item)

		if new_list:
			self.channelMenuList.setList(list(map(ListEntry, new_list)))
		else:
			no_results_msg = _("No search results, please change filter options...")
			self.channelMenuList.setList(list(map(ListEntry, [(no_results_msg,)])))

	def keyOK(self):
		if not self.piconlist or self.keyLocked:
			return
		current = self['list'].getCurrent()
		if current is None or len(current[0]) < 6:
			return
		self.selectedSetId = current[0][4]
		self.selectedDirUrl = current[0][1]
		self.picon_list_file = self.listFilePath(current[0])
		if exists(self.picon_list_file):
			self.getPiconFiles()
		else:
			url = current[0][5]
			list_file = self.picon_list_file

			def done(ok):
				if self.closed or list_file != self.picon_list_file:
					return
				if ok:
					self.getPiconFiles()
				else:
					self.dataError(url)

			threads.deferToThread(fetchFile, url, list_file).addCallback(done)

	def listFilePath(self, entry):
		"""Local copy of the picon list of a picon list entry."""
		return f"{self.piconTempDir}{entry[4]}_list"

	def getPiconFiles(self):
		if not exists(self.picon_list_file):
			return
		if self.prev_sel != self.picon_list_file:
			self.prev_sel = self.picon_list_file
			with open(self.picon_list_file, encoding="utf-8", errors="replace") as f:
				self.picon_files = [line.strip() for line in f if line.strip()]
		if self.picon_files:
			self.downloadPiconPath = f"{self.piconTempDir}{self.selectedSetId}.png"
			self.downloadPreview(f"{self.selectedDirUrl}/{quote(choice(self.picon_files))}", self.downloadPiconPath)
		else:
			print("[PiconManager] Empty picon list file")
			self['piconerror'].setText(_("No picons available in selected list"))

	def keyCancel(self):
		config.plugins.piconmanager.savetopath.value = self.picondir
		config.plugins.piconmanager.savetopath.save()
		self.closed = True
		self.channelMenuList.setList([])
		with suppress(OSError):
			rmtree(self.piconTempDir)
		self.close()

	def keyYellow(self):
		if self.downloading:
			return
		self.session.openWithCallback(self.selectedMediaFile, PiconManagerFolderScreen, self.picondir)

	def changeDrive(self):
		if self.downloading:
			return
		paths = PICON_PATHS
		current = self.picondir.rstrip("/")
		if current in paths:
			idx = paths.index(current) + 1
			if idx >= len(paths):  # after the last drive: let the user choose a folder
				self.keyYellow()
				return
		else:
			idx = 0
		self.picondir = paths[idx]
		self.piconfolder = join(self.picondir, "")
		self["piconpath2"].setText(self.piconfolder)
		print(f"[PiconManager] set picon path to: {self.piconfolder}")
		self.getFreeSpace()

	def setPiconNames(self, nameList, reducedList):
		"""Index the picon names of a set for comparableChannelName()."""
		self.nameSet = set(nameList)
		fullList = [interoperableName(x) for x in nameList]
		self.fullNames = uniqueIndex(zip(fullList, nameList))
		# only base logos ("WDR", not "BBC One HD") stand in for the reduced name of another channel
		self.reducedNames = uniqueIndex((reduced, name) for reduced, full, name in zip(reducedList, fullList, nameList) if reduced == full)

	def comparableChannelName(self, channelName: str):
		"""The picon name of the set for a channel: the same name, else the same name in other spelling
		("RTL Nitro HD" / "RTL NITRO HD"), else the base logo of the reduced name (regional variants:
		"WDR Köln" -> "WDR") when it is unique. Without a match the channel name itself."""
		if channelName in self.nameSet:
			return channelName
		return self.fullNames.get(interoperableName(channelName)) or self.reducedNames.get(reducedName(channelName)) or channelName

	def primaryByName(self, channelName: str):
		"""The by-name picon file name (without .png) of a channel: an existing file of this channel in other
		spelling, else the VTi name. A reduced name ("RTL" for "RTL Nitro") belongs to another channel, its
		picon must not be overwritten."""
		fullName = interoperableName(channelName)
		for variant in [channelName] + getInteroperableNames(channelName):
			if "/" not in variant and interoperableName(variant) == fullName and exists(join(self.piconfolder, f"{variant}.png")):
				return variant
		return VTiName(channelName)[:-4]

	def drivePresent(self):
		"""False if the picon folder is on a drive (/media/xxx) that is not mounted."""
		parts = self.picondir.split("/")
		if len(parts) > 2 and parts[1] == "media":
			return ismount("/".join(parts[:3]))
		return True

	def downloadPicons(self, result=None):
		if self.keyLocked or self.downloading:
			return
		current_item = self['list'].getCurrent()
		if not current_item or len(current_item[0]) < 6 or not self.countchlist:
			return

		if result is None:
			self.session.openWithCallback(
				self.downloadPicons,
				MessageBox,
				_("Do you want to download the selected picons?"),
				MessageBox.TYPE_YESNO
			)
			return

		if not result:
			return

		if not self.drivePresent():
			txt = f"{self.picondir}\n{_('is not installed.')}"
			self.session.open(MessageBox, txt, MessageBox.TYPE_INFO, timeout=3)
			return

		try:
			makedirs(self.picondir, exist_ok=True)
		except OSError as e:
			self.session.open(MessageBox, _("Error creating folder:") + f"\n{str(e)}", MessageBox.TYPE_ERROR)
			return

		self.downloading = True
		self.keyLocked = True
		self.cancelled = False
		self.countload = 0
		self.counterrors = 0
		self.countskipped = 0
		self['piconslidername'].setText("")
		self['piconpath2'].setText(_("loading"))
		notfoundWrite(f"{current_item[0][0]}\n{'#' * 50}", "w")

		if piconSetMode(current_item[0]) != MODE_REF:
			d = threads.deferToThread(loadPiconNames, current_item[0][5], self.listFilePath(current_item[0]))
		else:
			d = defer.succeed(([], []))
		d.addCallback(self.startDownloads, current_item)
		d.addErrback(self.downloadFailed)

	def startDownloads(self, nameLists, current_item):
		if self.closed:
			self.downloading = False
			return
		self.setPiconNames(*nameLists)
		mode = piconSetMode(current_item[0])
		baseUrl = f"{current_item[0][1]}/"
		available = self.nameSet

		jobs = []
		targets = set()
		for channel in self.chlist:
			job = self.getDownloadPaths(channel, mode, baseUrl, available)
			# one download per target file (same name in two bouquets, IPTV and DVB with the same picon name ...)
			target = tuple(job[0]) if job else None
			if job and (not target or target not in targets):
				targets.add(target)
				jobs.append(job)

		self.total_downloads = len(jobs)
		if not jobs:
			self.downloadFinished(None)
			return

		self.activityslider.setRange((0, self.total_downloads))
		self.activityslider.setValue(0)
		self['piconslidername'].setText(_("Download Progress"))
		self["piconslider"].show()
		self.progressDialog = self.session.openWithCallback(self.progressDialogClosed, PiconDownloadScreen, current_item[0][0], self.total_downloads)
		self.updateProgress()

		ds = defer.DeferredSemaphore(tokens=10)
		downloads = []
		for candidates, otherPath, label in jobs:
			d = ds.run(threads.deferToThread, self.downloadJob, candidates)
			d.addBoth(self.downloadDone, otherPath, label)
			downloads.append(d)

		defer.DeferredList(downloads).addCallback(self.downloadFinished)

	def progressDialogClosed(self, cancelled=False):
		self.progressDialog = None
		if cancelled and self.downloading:
			self.cancelled = True

	def downloadJob(self, candidates):
		"""Try the (url, path) candidates of a channel: the path of the downloaded picon, None when the set has
		no picon for it, False when the download was cancelled."""
		for url, path in candidates:
			if self.closed or self.cancelled:  # screen closed or download cancelled: skip the remaining downloads
				return False
			if fetchFile(url, path, png=True):
				return path
		return None

	def downloadDone(self, result, otherPath, label):
		if isinstance(result, str):
			self.countload += 1
			self.removeDouble(result, otherPath)
		elif result is False:
			self.countskipped += 1
		else:
			self.counterrors += 1
			notfoundWrite(label)
		if not self.closed:
			self.updateProgress(label)

	def updateProgress(self, label=""):
		done = self.countload + self.counterrors + self.countskipped
		now = monotonic()
		if done < self.total_downloads and now - self.lastProgressUpdate < 0.2:  # at most 5 redraws a second
			return
		self.lastProgressUpdate = now
		self.activityslider.setValue(done)
		self['picondownload'].setText(_("Picons loaded: ") + f" {self.countload}")
		self['piconerror'].setText(_("Picons not found: ") + f" {self.counterrors}")
		if self.progressDialog:
			self.progressDialog.update(done, self.countload, self.counterrors, label)

	def downloadFinished(self, result):
		self.downloading = False
		if self.progressDialog:
			self.progressDialog.close()
			self.progressDialog = None
		if self.closed:
			return
		self.keyLocked = False
		self['piconslidername'].setText(_("Download cancelled") if self.cancelled else _("Download Completed"))
		self["piconpath2"].setText(self.piconfolder)
		self.activityslider.setValue(self.total_downloads)
		self.getFreeSpace()
		message = (_("Download cancelled") if self.cancelled else _("Downloads completed")) + "\n" + _("Success: %d") % self.countload + "\n" + _("Errors: %d") % self.counterrors
		if self.countskipped:
			message += "\n" + _("Skipped: %d") % self.countskipped
		self.session.open(MessageBox, message, MessageBox.TYPE_INFO, timeout=10)

	def downloadFailed(self, failure):
		print(f"[PiconManager] Download error: {failure.getErrorMessage()}")
		self.downloading = False
		if self.progressDialog:
			self.progressDialog.close()
			self.progressDialog = None
		if not self.closed:
			self.keyLocked = False
			self["piconpath2"].setText(self.piconfolder)

	def getDownloadPaths(self, channel, mode, baseUrl, available=None):
		"""Download job of a channel: ([(url, path), ...] tried in order, picon to remove after a download, label).

		MODE_REF: the service reference picon.
		MODE_NAME: the VTi by-name picon, its service reference picon gets removed.
		MODE_SNP: the service name picon (SNP) like the picon renderer looks it up, picked from the picon list of
		the set (without list: the SNP name with and without HD suffix are tried); saved with the service
		reference name or the SNP name (setting), with the SNP name the service reference picon gets removed.
		"""
		try:
			if not channel or len(channel) < 2 or not channel[1] or channel[1] == '<n/a>':
				return None
			refName = piconRefName(channel[0])
			if not refName:
				return None
			refPath = join(self.piconfolder, f"{refName}.png")
			channelName = cleanServiceName(channel[1])
			label = f"{channelName} ({refName})"
			if mode == MODE_REF:
				return [(baseUrl + f"{refName}.png", refPath)], None, label

			if mode == MODE_NAME:
				namePath = join(self.piconfolder, f"{self.primaryByName(channelName)}.png")
				url = baseUrl + quote(f"{self.comparableChannelName(channelName)}.png")
				return [(url, namePath)], refPath, label

			saveRef = config.plugins.piconmanager.snpsave.value == "ref"
			if available:
				if refName in available:  # some SNP sets contain service reference picons too
					return [(baseUrl + f"{refName}.png", refPath)], None, label
				names = [x for x in piconSnpNames(channelName) if x in available][:1]
			else:
				names = piconLegacyNames(channelName)
			candidates = [(baseUrl + quote(f"{x}.png"), refPath if saveRef else join(self.piconfolder, f"{x}.png")) for x in names]
			return candidates, None if saveRef else refPath, label

		except Exception as e:
			print(f"[PiconManager] Error in getDownloadPaths: {str(e)}")
			return None

	def removeDouble(self, path, otherPath):
		"""A service reference picon is found before a by-name / SNP one: remove it for a freshly downloaded
		by-name / SNP picon, so the downloaded one gets shown."""
		if otherPath and otherPath != path and (exists(otherPath) or islink(otherPath)):
			try:
				remove(otherPath)
			except OSError as e:
				print(f"[PiconManager] Error removing {otherPath}: {str(e)}")

	def dataError2(self, error=None):
		if self.closed:
			return
		print(f"[PiconManager] Error loading picon list: {error}")
		errorWrite(f"{self.server_url}\n{error}")
		self.tried_mirrors.append(self.server_url)
		for x in server_choices:
			if x[0] not in self.tried_mirrors:
				self.server_url = x[0]
				self.getPiconList()
				return
		self.channelMenuList.setList(list(map(ListEntry, [(_("Sorry, service is temporarily unavailable"),)])))

	def dataError(self, url):
		print(f"[PiconManager] ERROR: download failed {url}")
		errorWrite(f"download failed: {url}")
		if not self.closed:
			self["picon"].hide()

	def showPiconFile(self, picPath):
		showPiconPixmap(self["picon"], picPath)


class PiconDownloadScreen(Screen):
	"""Progress of a picon download: bar, counters, last channel; RED / EXIT cancels the download."""

	skin = scaleSkin("""<screen name="PiconDownloadScreen" title="Download picons" position="center,center" size="760,250">
		<widget name="setname" position="20,15" size="720,30" font="Regular;22" foregroundColor="#00fba207" transparent="1" noWrap="1" />
		<widget name="progress" position="20,60" size="720,22" borderWidth="1" borderColor="#00f8f2e6" foregroundColor="#00fba207" />
		<widget name="status" position="20,95" size="720,30" font="Regular;22" transparent="1" />
		<widget name="channel" position="20,130" size="720,30" font="Regular;20" foregroundColor="#00f8f2e6" transparent="1" noWrap="1" />
		<ePixmap position="20,205" size="60,25" zPosition="3" pixmap="{pic}button_red.png" transparent="1" alphatest="on" />
		<widget name="key_red" position="52,205" size="300,25" font="Regular;20" transparent="1" zPosition="3" />
	</screen>""")

	def __init__(self, session, setName, total):
		Screen.__init__(self, session)
		self.total = total
		self.setTitle(_("Download picons"))
		self["setname"] = Label(setName)
		self["progress"] = ProgressBar()
		self["progress"].setRange((0, total))
		self["status"] = Label()
		self["channel"] = Label()
		self["key_red"] = Label(_("Cancel download"))
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
			"cancel": self.cancel,
			"red": self.cancel,
		}, -1)
		self.update(0, 0, 0)

	def update(self, done, loaded, notFound, label=""):
		self["progress"].setValue(done)
		self["status"].setText(_("%d of %d - loaded: %d, not found: %d") % (done, self.total, loaded, notFound))
		if label:
			self["channel"].setText(label)

	def cancel(self):
		self.close(True)


class PicRemoverScreen(Screen):
	"""Lists the picons of the picon folder no channel of the bouquets uses and deletes them.

	A picon counts as used when a picon renderer would find it for one of the channels (service
	reference names with their fallbacks, utf8 / SNP names, VTi by-name names). Symlinks are removed
	as links, a file a used symlink points to is kept.
	"""

	skin = scaleSkin(screenHeader("PicRemoverScreen", "Picon Remover") + """
		<widget name="piconpath" position="21,14" size="220,30" font="Regular;24" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="right" />
		<widget name="piconpath2" position="244,14" size="500,30" font="Regular;24" foregroundColor="#00f8f2e6" transparent="1" zPosition="3" halign="left" />
		<widget name="piconcount" position="745,397" size="400,30" font="Regular;24" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="center" />
		<widget name="picon" position="740,67" size="400,240" zPosition="3" transparent="1" borderWidth="0" borderColor="#0000000" alphatest="blend" />
		<widget name="list" position="20,54" size="700,520" transparent="1" scrollbarMode="showOnDemand" />
		<widget name="info" position="745,356" size="400,30" font="Regular;24" foregroundColor="#00fff000" transparent="1" zPosition="3" halign="center" />
		<widget name="key_red" position="42,615" size="200,25" transparent="1" font="Regular;22" />
		<widget name="key_green" position="265,615" size="200,25" transparent="1" font="Regular;22" />
		<ePixmap position="10,615" size="60,25" zPosition="3" pixmap="{pic}button_red.png" transparent="1" alphatest="on" />
		<ePixmap position="227,615" size="60,25" zPosition="3" pixmap="{pic}button_green.png" transparent="1" alphatest="on" />
	</screen>""")

	def __init__(self, session, picon_path):
		Screen.__init__(self, session)
		self.skinName = "PicRemoverScreen"
		self.piconfolder = picon_path
		self.unused_picons_list = []
		self.setTitle(f"{pname}   {_('V')} {pversion}")
		self['list'] = createPiconMenuList()
		self['list'].onSelectionChanged.append(self.showPic)
		self["info"] = Label()
		self['piconpath'] = Label(_("Picon folder: "))
		self['piconcount'] = Label(_("Reading Picons..."))
		self['piconpath2'] = Label(self.piconfolder)
		self['picon'] = Pixmap()
		self["key_red"] = Label(_("Cancel"))
		self["key_green"] = Label(_("Delete"))
		self["actions"] = ActionMap(["OkCancelActions", "ColorActions"], {
			"red": self.close,
			"green": self.askRemoval,
			"cancel": self.close
		}, -1)

		self.channel_refs = set()
		self.onLayoutFinish.append(self.start_workflow)

	def start_workflow(self):
		if not isdir(self.piconfolder):
			self['info'].setText(_("Invalid path!"))
			self['piconcount'].setText("")
			return

		channel_list = buildChannellist(allAlternatives=True)
		self.channel_refs = self.generate_picon_refs(channel_list)
		# without channels every picon would count as unused
		self.unused_picons_list = self.find_unused(self.piconfolder, self.channel_refs) if channel_list else []
		print(f"[PiconManager] unused picons: {len(self.unused_picons_list)}")
		self._update_ui()

	@staticmethod
	def find_unused(folder, used_names):
		"""Picon file names of folder not in used_names (lower case). A file a used symlink points to
		is not listed, it is needed."""
		files = [f for f in listdir(folder) if f.lower().endswith('.png') and f.lower() not in PROTECTED_PICONS]
		used_targets = set()
		for f in files:
			path = join(folder, f)
			if f.lower() in used_names and islink(path):
				used_targets.add(realpath(path))
		unused = []
		for f in files:
			if f.lower() in used_names:
				continue
			path = join(folder, f)
			if not islink(path) and realpath(path) in used_targets:
				continue
			unused.append(f)
		return sorted(unused, key=str.lower)

	@staticmethod
	def generate_picon_refs(channel_list):
		refs = set()
		for serviceref, servicename in channel_list:
			names = []
			for base in piconRefNames(serviceref):
				names.append(base)
				names.extend(f"{base}{suffix}" for suffix in ("~", "-hd", "_hd", "_fhd", "-4k", "_uhd"))
				names.extend(f"{base}~{i}" for i in range(5))
			name = cleanServiceName(servicename)
			if name:
				names.extend(piconSnpNames(name))
				names.append(VTiName(name)[:-4])
				names.append(correctedFileName(name))
				names.extend(getInteroperableNames(name))
				names.append(sub(r'[^\w\-]', '', name.replace(' ', '_')))
			refs.update(f"{x}.png".lower() for x in names if x)
		return refs

	def _update_ui(self):
		count = len(self.unused_picons_list)
		self['piconcount'].setText(_("Picons to be deleted: {}").format(count))
		if count == 0:
			self["info"].setText(_("No picons to delete"))
			self["list"].setList(list(map(ListEntry, [(_("No picons to delete"),)])))
		else:
			self["info"].setText(_("Picons found: {}").format(count))
			self["list"].setList([ListEntry((x,)) for x in self.unused_picons_list])
			self.showPic()

	def askRemoval(self):
		if not self.unused_picons_list:
			return
		self.session.openWithCallback(
			self.executeRemoval,
			MessageBox,
			_("Delete %d unused picons?") % len(self.unused_picons_list),
			MessageBox.TYPE_YESNO,
			default=False
		)

	def executeRemoval(self, answer=True):
		if not answer:
			return
		deleted = 0
		errors = 0
		for picon in self.unused_picons_list:
			path = join(self.piconfolder, picon)
			try:
				remove(path)  # a symlink gets removed itself, not its target
				deleted += 1
			except OSError as e:
				print(f"[PiconManager] Error deleting {path}: {str(e)}")
				errors += 1
		errorWrite(f"Valid References: {sorted(self.channel_refs)}\nRemoved Files: {self.unused_picons_list}")
		self._show_result(deleted, errors)

	def _show_result(self, deleted, errors):
		msg = _("Operation completed!") + "\n"
		msg += _("Deleted: {}").format(deleted) + "\n"
		msg += _("Errors: {}").format(errors)
		self.session.openWithCallback(
			lambda *args: self.close(deleted),
			MessageBox,
			msg,
			MessageBox.TYPE_INFO
		)

	def showPic(self):
		current_index = self["list"].l.getCurrentSelectionIndex()
		path = None
		if 0 <= current_index < len(self.unused_picons_list):
			path = join(self.piconfolder, self.unused_picons_list[current_index])
		showPiconPixmap(self["picon"], path)


class PiconManagerFolderScreen(Screen):
	skin = scaleSkin(screenHeader("PiconManagerFolderScreen", "Choose Picon folder") + """
		<widget name="media" position="21,9" size="700,40" font="Regular;24" foregroundColor="#00fba207" transparent="1" zPosition="3" halign="center" />
		<widget name="folderlist" position="20,54" size="700,520" itemHeight="35" font="Regular;28" transparent="1" scrollbarMode="showOnDemand" />
		<widget name="key_red" position="42,615" size="200,25" transparent="1" font="Regular;22" zPosition="3"  />
		<widget name="key_green" position="265,615" size="200,25" transparent="1" font="Regular;22" zPosition="3"  />
		<ePixmap position="767,104" size="350,210" pixmap="{pic}pmanager.png" alphatest="on" />
		<ePixmap position="10,615" size="60,25" zPosition="3" pixmap="{pic}button_red.png" transparent="1" alphatest="on" />
		<ePixmap position="227,615" size="60,25" zPosition="3" pixmap="{pic}button_green.png" transparent="1" alphatest="on" />
	</screen>""")

	def __init__(self, session, initDir):
		Screen.__init__(self, session)
		if not initDir or not isdir(initDir):
			initDir = "/usr/share/enigma2/"
		self.setTitle(_("Choose Picon folder"))
		self["folderlist"] = FileList(initDir, inhibitMounts=False, inhibitDirs=False, showMountpoints=False, showFiles=False)
		self["media"] = Label()
		self["key_green"] = Label(_("OK"))
		self["key_red"] = Label(_("Cancel"))
		self["actions"] = ActionMap(
			["WizardActions", "DirectionActions", "ColorActions", "EPGSelectActions"],
			{
				"back": self.cancel,
				"left": self.left,
				"right": self.right,
				"up": self.up,
				"down": self.down,
				"ok": self.ok,
				"green": self.green,
				"red": self.cancel
			},
			-1
		)

	def cancel(self):
		self.close(None)

	def green(self):
		selection = self["folderlist"].getSelection()
		if not selection or not selection[0]:
			return
		self.close(join(selection[0], ""))

	def up(self):
		self["folderlist"].up()
		self.updateFile()

	def down(self):
		self["folderlist"].down()
		self.updateFile()

	def left(self):
		self["folderlist"].pageUp()
		self.updateFile()

	def right(self):
		self["folderlist"].pageDown()
		self.updateFile()

	def ok(self):
		if self["folderlist"].canDescent():
			self["folderlist"].descent()
			self.updateFile()

	def updateFile(self):
		selection = self["folderlist"].getSelection()
		self["media"].setText(selection[0] if selection and selection[0] else "")


class pm_conf(ConfigListScreen, Screen, HelpableScreen):
	skin = scaleSkin(screenHeader("pm_conf", "PiconManager - Settings") + """
		<widget name="config" position="20,54" size="700,520" itemHeight="35" font="Regular;28" transparent="1" scrollbarMode="showOnDemand" />
		<widget name="key_red" position="42,615" size="200,25" transparent="1" font="Regular;22" zPosition="3"  />
		<widget name="key_green" position="265,615" size="200,25" transparent="1" font="Regular;22" zPosition="3"  />
		<ePixmap position="767,104" size="350,210" pixmap="{pic}pmanager.png" alphatest="on" />
		<ePixmap position="10,615" size="60,25" zPosition="3" pixmap="{pic}button_red.png" transparent="1" alphatest="on" />
		<ePixmap position="227,615" size="60,25" zPosition="3" pixmap="{pic}button_green.png" transparent="1" alphatest="on" />
	</screen>""")

	def __init__(self, session):
		self.liste = []
		self.size = config.plugins.piconmanager.size.value
		self.creator = config.plugins.piconmanager.creator.value
		self.bit = config.plugins.piconmanager.bit.value
		self.server = config.plugins.piconmanager.server.value
		self.alter = config.plugins.piconmanager.alter.value
		Screen.__init__(self, session)
		HelpableScreen.__init__(self)
		ConfigListScreen.__init__(self, self.liste, on_change=self.load_list)
		self.setTitle(_("PiconManager - Settings"))
		self["key_green"] = Label(_("OK"))
		self["key_red"] = Label(_("Cancel"))

		self["SetupActions"] = HelpableActionMap(
			self, "SetupActions",
			{
				"cancel": (self.cancel, _("Cancel")),
				"ok": (self.save, _("OK and exit")),
			},
			-1
		)

		self["ColorActions"] = HelpableActionMap(
			self, "ColorActions",
			{
				"green": (self.save, _("OK and exit")),
				"red": (self.cancel, _("Cancel")),
			},
			-1
		)

		self.onLayoutFinish.append(self.load_list)

	def load_list(self):
		self.liste = []
		if len(server_choices) > 1:
			self.liste.append(getConfigListEntry(_("Select Server:"), config.plugins.piconmanager.server))
		self.liste.append(getConfigListEntry(_("Set Filter:"),))
		self.liste.append(getConfigListEntry(_("Size"), config.plugins.piconmanager.size))
		self.liste.append(getConfigListEntry(_("Creator"), config.plugins.piconmanager.creator))
		self.liste.append(getConfigListEntry(_("Color depth: "), config.plugins.piconmanager.bit))
		self.liste.append(getConfigListEntry(_("Not older than X days:"), config.plugins.piconmanager.alter))
		self.liste.append(getConfigListEntry(_("Save service name picons (SNP) as:"), config.plugins.piconmanager.snpsave))
		self.liste.append(getConfigListEntry("------ " + _("Option:") + " ------",))
		self.liste.append(getConfigListEntry(_("Remember permanently?"), config.plugins.piconmanager.saving))
		self.liste.append(getConfigListEntry(_("Activate debug logging?"), config.plugins.piconmanager.debug))
		self["config"].setList(self.liste)

	def save(self):
		try:
			self.size = config.plugins.piconmanager.size.value
			self.creator = config.plugins.piconmanager.creator.value
			self.bit = config.plugins.piconmanager.bit.value
			self.alter = config.plugins.piconmanager.alter.value
			reload_picons = False

			if len(server_choices) > 1 and self.server != config.plugins.piconmanager.server.value:
				reload_picons = True
				self.server = config.plugins.piconmanager.server.value

			config.plugins.piconmanager.saving.save()

			for x in self.liste:
				if len(x) >= 2:
					if config.plugins.piconmanager.saving.value:
						x[1].save()
					else:
						x[1].cancel()

			self.close(
				self.creator,
				self.size,
				self.bit,
				self.server,
				True,
				reload_picons,
				self.alter
			)

		except Exception as e:
			print(f"[PiconManager] Error saving piconmanager configuration: {e}")

	def cancel(self):
		for x in self.liste:
			if len(x) >= 2:
				x[1].cancel()
		self.close(self.creator, self.size, self.bit, self.server, False, False)


def main(session, **kwargs):
	session.open(PiconManagerScreen)


def Plugins(**kwargs):
	return PluginDescriptor(name=pname, description=pdesc, where=PluginDescriptor.WHERE_PLUGINMENU, icon="plugin.png", fnc=main)
