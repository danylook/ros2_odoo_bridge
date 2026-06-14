from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'ros2_odoo_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'msg'),    glob('msg/*.msg')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Daniel',
    maintainer_email='robot@tudominio.com',
    description='Bridge ROS2 Jazzy <-> Odoo 18 via HTTPS',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'bridge_node = ros2_odoo_bridge.bridge_node:main',
            'job_server   = ros2_odoo_bridge.job_server:main',
        ],
    },
)
