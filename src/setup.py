from setuptools import setup
import setup_translate

pkg = 'Extensions.PiconManager'
setup(name='enigma2-plugin-extensions-piconmanager',
       version='3.0',
       description='Picon Manager by VTi',
       package_dir={pkg: 'PiconManager'},
       packages=[pkg],
       package_data={pkg: ['images/*.png', '*.png', '*.xml', 'locale/*/LC_MESSAGES/*.mo', 'plugin.png', 'pic/*.png']},
       cmdclass=setup_translate.cmdclass,  # for translation
      )
