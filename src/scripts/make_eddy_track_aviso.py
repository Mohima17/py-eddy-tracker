# -*- coding: utf-8 -*-

"""
===============================================================================
This file is part of py-eddy-tracker.

    py-eddy-tracker is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    py-eddy-tracker is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with py-eddy-tracker.  If not, see <http://www.gnu.org/licenses/>.

Copyright (c) 2014-2015 by Evan Mason
Email: emason@imedea.uib-csic.es
===============================================================================

make_eddy_track_AVISO.py

Version 2.0.3

===============================================================================
"""

from glob import glob
from yaml import load as yaml_load
from datetime import datetime
from netCDF4 import Dataset
from argparse import ArgumentParser
from scipy.ndimage import gaussian_filter
from matplotlib.dates import date2num, num2julian
from matplotlib.figure import Figure
import logging
import numpy as np

from py_eddy_tracker.py_eddy_tracker_classes import \
    collection_loop, func_hann2d_fast
from py_eddy_tracker.py_eddy_tracker_property_classes import SwirlSpeed
from py_eddy_tracker.grid.aviso import AvisoGrid
from py_eddy_tracker.make_eddy_tracker_list_obj import SearchEllipse, TrackList
from py_eddy_tracker.global_tracking import GlobalTracking


class ColoredFormatter(logging.Formatter):
    COLOR_LEVEL = dict(
        CRITICAL="\037[37;41m",
        ERROR="\033[31;47m",
        WARNING="\033[30;47m",
        INFO="\033[36m",
        DEBUG="\033[34m",
        )

    def __init__(self, message):
        super(ColoredFormatter, self).__init__(message)

    def format(self, record):
        color = self.COLOR_LEVEL.get(record.levelname, '')
        color_reset = '\033[0m'
        model = color + '%s' + color_reset
        record.msg = model % record.msg
        record.funcName = model % record.funcName
        record.module = model % record.module
        record.levelname = model % record.levelname
        return super(ColoredFormatter, self).format(record)

if __name__ == '__main__':
    FORMAT_LOG = "%(levelname)-8s %(asctime)s %(module)s." \
                 "%(funcName)s :\n\t\t\t\t\t%(message)s"
    # set up logging to CONSOLE
    CONSOLE = logging.StreamHandler()
    CONSOLE.setFormatter(ColoredFormatter(FORMAT_LOG))
    # add the handler to the root logger
    logging.getLogger('').addHandler(CONSOLE)

    # Run using:
    PARSER = ArgumentParser("Tool to detect and track eddies. "
                            "To run use 'make_eddy_track_AVISO.py "
                            "eddy_tracker_configuration.yaml'")
    PARSER.add_argument('yaml_file',
                        help='Yaml file to configure py-eddy-tracker')
    OPTS = PARSER.parse_args()
    YAML_FILE = OPTS.yaml_file

    # Read yaml configuration file
    with open(YAML_FILE, 'r') as stream:
        CONFIG = yaml_load(stream)

    logging.getLogger().setLevel(getattr(logging, CONFIG['VERBOSE'].upper()))
    VERBOSE = 'No' if CONFIG['VERBOSE'].upper() != 'DEBUG' else 'Yes'

    logging.info('Launching with yaml file: %s', YAML_FILE)

    # Setup configuration
    DATA_DIR = CONFIG['PATHS']['DATA_DIR']
    SAVE_DIR = CONFIG['PATHS']['SAVE_DIR']
    logging.info('Outputs saved to %(SAVE_DIR)s', CONFIG['PATHS'])

    DIAGNOSTIC_TYPE = CONFIG['DIAGNOSTIC_TYPE']
    if DIAGNOSTIC_TYPE not in ['SLA', 'Q']:
        raise Exception('Unknown diagnostic %s', DIAGNOSTIC_TYPE)

    CONFIG['THE_DOMAIN'] = CONFIG['DOMAIN']['THE_DOMAIN']

    # It is not recommended to change values given below
    # for 'Global', 'BlackSea' or 'MedSea'...
    if 'Global' in CONFIG['THE_DOMAIN']:
        CONFIG['lonmin'] = -100.
        CONFIG['lonmax'] = 290.
        CONFIG['latmin'] = -80.
        CONFIG['latmax'] = 80.

    elif CONFIG['THE_DOMAIN'] in ('Regional', 'MedSea'):
        CONFIG['lonmin'] = CONFIG['DOMAIN']['LONMIN']
        CONFIG['lonmax'] = CONFIG['DOMAIN']['LONMAX']
        CONFIG['latmin'] = CONFIG['DOMAIN']['LATMIN']
        CONFIG['latmax'] = CONFIG['DOMAIN']['LATMAX']

    START_DATE = CONFIG['DATE_STR'] = CONFIG['DOMAIN']['DATE_STR']
    END_DATE = CONFIG['DATE_END'] = CONFIG['DOMAIN']['DATE_END']
    if START_DATE > END_DATE:
        raise Exception('DATE_END must be larger than DATE_STR')

    AVISO_DT14 = CONFIG['AVISO']['AVISO_DT14']
    if AVISO_DT14:
        PRODUCT = 'AVISO_DT14'
        AVISO_DT14_SUBSAMP = CONFIG['AVISO']['AVISO_DT14_SUBSAMP']
        if AVISO_DT14_SUBSAMP:
            DAYS_BTWN_RECORDS = CONFIG['AVISO']['DAYS_BTWN_RECORDS']
        else:
            DAYS_BTWN_RECORDS = 1.
    else:
        PRODUCT = 'AVISO_DT10'
        DAYS_BTWN_RECORDS = 7.  # old seven day AVISO
    CONFIG['DAYS_BTWN_RECORDS'] = DAYS_BTWN_RECORDS
    AVISO_FILES = CONFIG['AVISO']['AVISO_FILES']

    if 'SLA' in DIAGNOSTIC_TYPE:
        MAX_SLA = CONFIG['CONTOUR_PARAMETER'
                         ]['CONTOUR_PARAMETER_SLA']['MAX_SLA']
        INTERVAL = CONFIG['CONTOUR_PARAMETER'
                          ]['CONTOUR_PARAMETER_SLA']['INTERVAL']
        CONFIG['CONTOUR_PARAMETER'] = np.arange(-MAX_SLA, MAX_SLA + INTERVAL,
                                                INTERVAL)
        # AMPMIN = CONFIG['AMPMIN']
        # AMPMAX = CONFIG['AMPMAX']
        AMP0 = CONFIG['AMP0']

    elif 'Q' in DIAGNOSTIC_TYPE:
        MAX_Q = CONFIG['CONTOUR_PARAMETER']['CONTOUR_PARAMETER_Q']['MAX_Q']
        NUM_LEVS = CONFIG['CONTOUR_PARAMETER'
                          ]['CONTOUR_PARAMETER_Q']['NUM_LEVS']
        CONFIG['CONTOUR_PARAMETER'] = np.linspace(0, MAX_Q, NUM_LEVS)[::-1]
        AMPMIN = 0.02  # max(abs(xi/f)) within the eddy
        AMPMAX = 100.
        AMP0 = 0.02  # vort/f

    SMOOTHING = CONFIG['SMOOTHING']
    if SMOOTHING:
        if 'SLA' in DIAGNOSTIC_TYPE:
            ZWL = np.atleast_1d(CONFIG['SMOOTHING_SLA']['ZWL'])
            MWL = np.atleast_1d(CONFIG['SMOOTHING_SLA']['MWL'])
        elif 'Q' in DIAGNOSTIC_TYPE:
            SMOOTH_FAC = CONFIG['SMOOTHING_Q']['SMOOTH_FAC']
        SMOOTHING_TYPE = CONFIG['SMOOTHING_SLA']['TYPE']

    TEMP0 = CONFIG['TEMP0']
    SALT0 = CONFIG['SALT0']

    # End user configuration setup options
    # --------------------------------------------------------------------------

    # Get complete AVISO file list
    AVISO_FILES = sorted(glob(DATA_DIR + AVISO_FILES))
    logging.info('%d grids available', len(AVISO_FILES))

    # For subsampling to get identical list as old_AVISO use:
    # AVISO_FILES = AVISO_FILES[5:-5:7]
    if AVISO_DT14 and AVISO_DT14_SUBSAMP:
        print AVISO_FILES
        AVISO_FILES = AVISO_FILES[5:-5:np.int(DAYS_BTWN_RECORDS)]

    # Set up a grid object using first AVISO file in the list
    SLA_GRD = AvisoGrid(AVISO_FILES[0], CONFIG['THE_DOMAIN'], PRODUCT,
                        CONFIG['lonmin'], CONFIG['lonmax'],
                        CONFIG['latmin'], CONFIG['latmax'])

    # Instantiate search ellipse object
    SEARCH_ELLIPSE = SearchEllipse(CONFIG['THE_DOMAIN'],
                                   SLA_GRD, DAYS_BTWN_RECORDS,
                                   CONFIG['PATHS']['RW_PATH'])

    if 'Gaussian' in SMOOTHING_TYPE:
        # Get parameters for gaussian_filter
        ZRES, MRES = SLA_GRD.gaussian_resolution(ZWL, MWL)

    # Initialise two eddy objects to hold data
    # kwargs = CONFIG
    A_EDDY = TrackList('Anticyclonic', SLA_GRD, SEARCH_ELLIPSE, **CONFIG)

    C_EDDY = TrackList('Cyclonic', SLA_GRD, SEARCH_ELLIPSE, **CONFIG)

    # See Chelton section B2 (0.4 degree radius)
    # These should give 8 and 1000 for 0.25 deg resolution
    PIXMIN = np.round((np.pi * CONFIG['RADMIN'] ** 2) /
                      SLA_GRD.resolution ** 2)
    PIXMAX = np.round((np.pi * CONFIG['RADMAX'] ** 2) /
                      SLA_GRD.resolution ** 2)
    logging.info('Pixel range = %s-%s', np.int(PIXMIN), np.int(PIXMAX))

    A_EDDY.pixel_threshold = [PIXMIN, PIXMAX]
    C_EDDY.pixel_threshold = [PIXMIN, PIXMAX]

    # Create nc files for saving of eddy tracks
    A_EDDY.create_netcdf(
        DATA_DIR,
        SAVE_DIR + 'eddy_tracks_%s_AVISO_anticyclonic.nc' % DIAGNOSTIC_TYPE)
    C_EDDY.create_netcdf(
        DATA_DIR,
        SAVE_DIR + 'eddy_tracks_%s_AVISO_cyclonic.nc' % DIAGNOSTIC_TYPE)

    # Get contours of Q/sla parameter
    CONT_FIG = Figure()
    CONT_AX = CONT_FIG.add_subplot(111)

    FIRST_RECORD = True

    START_TIME = datetime.now()

    logging.info('Start tracking')

    NB_STEP = 0
    for AVISO_FILE in AVISO_FILES:
        with Dataset(AVISO_FILE) as nc:
            if 'time' in nc.variables:
                grd_date = nc.variables['time'][:]
                grd_date += SLA_GRD.base_date
            else:
                grd_date = nc.OriginalName
                elt_split = 'qd_' if 'qd_' in grd_date else 'h_'
                grd_date = grd_date.partition(elt_split)[2].partition('_')[0]
        # Bad things
        grd_date = datetime.strptime(grd_date, '%Y%m%d').date()
        grd_int_date = num2julian(date2num(grd_date))

        if grd_date < START_DATE or grd_date > END_DATE:
            continue

        logging.info('AVISO_FILE : %s', AVISO_FILE)

        # Holding variables
        A_EDDY.reset_holding_variables()
        C_EDDY.reset_holding_variables()

        sla = SLA_GRD.get_aviso_data(AVISO_FILE)
        SLA_GRD.set_mask(sla).uvmask()

        if SMOOTHING:
            if 'Gaussian' in SMOOTHING_TYPE:
                logging.info('applying Gaussian high-pass filter')
                # Set landpoints to zero
                np.place(sla, SLA_GRD.mask, 0.)
                if hasattr(sla, 'data'):
                    np.place(sla, sla.data == SLA_GRD.fillval, 0.)
                # High pass filter, see
                # http://stackoverflow.com/questions/6094957/high-pass-filter-
                # for-image-processing-in-python-by-using-scipy-numpy
                sla -= gaussian_filter(sla, [MRES, ZRES])
                logging.info('applying Gaussian high-pass filter -- OK')

            elif 'Hanning' in SMOOTHING_TYPE:
                logging.info('applying %s passes of Hanning filter',
                             SMOOTH_FAC)
                # Do SMOOTH_FAC passes of 2d Hanning filter
                sla = func_hann2d_fast(sla, SMOOTH_FAC)
                logging.info('applying %s passes of Hanning filter -- OK')

            else:
                raise Exception('Filter unknown : %s', SMOOTHING_TYPE)

        # Apply the landmask
        sla.mask += SLA_GRD.mask

        # Multiply by 0.01 for m
        SLA_GRD.set_geostrophic_velocity(sla * 0.01)

        # Remove padded boundary
        sla = sla[SLA_GRD.view_unpad]

        # Calculate EKE
        SLA_GRD.get_eke()

        if 'SLA' in DIAGNOSTIC_TYPE:
            A_EDDY.sla = sla.copy()
            C_EDDY.sla = sla.copy()

        # Get scalar speed
        A_EDDY.uspd = SLA_GRD.uspd
        C_EDDY.uspd = SLA_GRD.uspd

        # Set interpolation coefficients
        SLA_GRD.set_interp_coeffs(sla, SLA_GRD.uspd)
        A_EDDY.sla_coeffs = SLA_GRD.sla_coeffs
        A_EDDY.uspd_coeffs = SLA_GRD.uspd_coeffs
        C_EDDY.sla_coeffs = SLA_GRD.sla_coeffs
        C_EDDY.uspd_coeffs = SLA_GRD.uspd_coeffs

        if 'SLA' in DIAGNOSTIC_TYPE:
            logging.info('Processing SLA contours for eddies')
            CONTOURS = CONT_AX.contour(
                SLA_GRD.lon,
                SLA_GRD.lat,
                A_EDDY.sla,
                levels=A_EDDY.contour_parameter)
            # Note that C_CS is in reverse order
            logging.info('Processing SLA contours for eddies -- Ok')
        else:
            raise Exception()

        # clear the current axis
        CONT_AX.cla()

        # Set contour coordinates and indices for calculation of
        # speed-based radius
        A_EDDY.swirl = SwirlSpeed(CONTOURS)
        C_EDDY.swirl = A_EDDY.swirl

        # Now we loop over the CS collection
        if 'SLA' in DIAGNOSTIC_TYPE:
            logging.info("Anticyclonic research")
            A_EDDY = collection_loop(CONTOURS, SLA_GRD, grd_int_date,
                                     a_list_obj=A_EDDY, c_list_obj=None)
            # Note that C_CS is reverse order
            logging.info("Cyclonic research")
            C_EDDY = collection_loop(CONTOURS, SLA_GRD, grd_int_date,
                                     a_list_obj=None, c_list_obj=C_EDDY)

        GlobalTracking(A_EDDY, grd_date).write_netcdf()
        GlobalTracking(C_EDDY, grd_date).write_netcdf()
        NB_STEP += 1
        exit()

        # Track the eddies
        A_EDDY.track_eddies(FIRST_RECORD)
        C_EDDY.track_eddies(FIRST_RECORD)

        # Save inactive eddies to nc file
        A_EDDY.write2netcdf(grd_int_date)
        C_EDDY.write2netcdf(grd_int_date)

        FIRST_RECORD = False

    A_EDDY.kill_all_tracks()
    C_EDDY.kill_all_tracks()

    A_EDDY.write2netcdf(grd_int_date, stopper=1)
    C_EDDY.write2netcdf(grd_int_date, stopper=1)

    # Total running time
    logging.info('Mean duration by loop : %s',
                 (datetime.now() - START_TIME) / NB_STEP)
    logging.info('Duration : %s', datetime.now() - START_TIME)

    logging.info('Outputs saved to %s', SAVE_DIR)