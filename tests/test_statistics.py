"""
Tests for the custom statistics functions

"""
from __future__ import absolute_import

import string

import hypothesis.strategies as st
import numpy as np
import pytest
import xarray as xr
from datetime import datetime
from hypothesis import given

import datacube_stats.statistics
from datacube.utils.geometry import CRS
from datacube_stats.statistics import nan_percentile, argpercentile, axisindex, NormalisedDifferenceStats, WofsStats, \
    StatsConfigurationError, Medoid


def test_nan_percentile():
    # create array of shape(5,100,100) - image of size 100x100 with 5 layers
    test_arr = np.random.randint(0, 10000, 50000).reshape(5, 100, 100).astype(np.float32)
    np.random.shuffle(test_arr)
    # place random NaNs
    random_nans = np.random.randint(0, 50000, 500).astype(np.float32)
    for r in random_nans:
        test_arr[test_arr == r] = np.NaN

    # Test with single q
    q = 45
    input_arr = np.array(test_arr, copy=True)
    std_np_func = np.nanpercentile(input_arr, q=q, axis=0)
    new_func = nan_percentile(input_arr, q=q)

    assert np.allclose(std_np_func, new_func)

    # Test with all qs
    qs = range(0, 100)
    input_arr = np.array(test_arr, copy=True)
    std_np_func = np.nanpercentile(input_arr, q=qs, axis=0)
    new_func = nan_percentile(input_arr, q=qs)

    assert np.allclose(std_np_func, new_func)


def test_argpercentile():
    # Create random Data
    test_arr = np.random.randint(0, 10000, 50000).reshape(5, 100, 100).astype(np.float32)
    np.random.shuffle(test_arr)
    # place random NaNs
    rand_nan = np.random.randint(0, 50000, 500).astype(np.float32)
    for r in rand_nan:
        test_arr[test_arr == r] = np.NaN

    np_result = np.nanpercentile(test_arr, q=25, axis=0, interpolation='nearest')
    argpercentile_result = axisindex(test_arr, argpercentile(test_arr, q=25, axis=0), axis=0)
    assert np.isclose(np_result, argpercentile_result).all()


def test_xarray_reduce():
    arr = np.random.random((100, 100, 5))
    dataarray = xr.DataArray(arr, dims=('x', 'y', 'time'))

    def reduction(in_arr, axis):
        assert axis == 2
        output = np.average(in_arr, axis)
        return output

    dataarray = dataarray.reduce(reduction, dim='time')

    assert dataarray.dims == ('x', 'y')


def test_masked_count():
    arr = np.random.randint(3, size=(5, 100, 100))
    da = xr.DataArray(arr, dims=('time', 'x', 'y'))

    from datacube_stats.statistics import MaskMultiCounter

    mc = MaskMultiCounter([{'name': 'test', 'mask': lambda x: x}])

    ds = xr.Dataset({'payload': da})
    result = mc.compute(ds)

    assert result


@pytest.mark.skipif(not hasattr(datacube_stats.statistics, 'NewGeomedianStatistic'),
                    reason='requires `pcm` module for new geomedian statistics')
def test_new_geometric_median():
    from datacube_stats.statistics import NewGeomedianStatistic

    arr = np.random.random((5, 100, 100))
    dataarray = xr.DataArray(arr, dims=('time', 'y', 'x'), coords={'time': list(range(5))})
    dataset = xr.Dataset(data_vars={'band1': dataarray, 'band2': dataarray})

    new_geomedian_stat = NewGeomedianStatistic()
    result = new_geomedian_stat.compute(dataset)

    assert result

    assert result.band1.dims == result.band2.dims == ('y', 'x')

    # The two bands had the same inputs, so should have the same result
    assert (result.band1 == result.band2).all()


DatacubeCRSStrategy = st.sampled_from([CRS('EPSG:4326'), CRS('EPSG:3577'), CRS('EPSG:28354')])


def ordered_dates(num):
    return st.lists(st.datetimes(datetime(1970, 1, 1), datetime(2050, 1, 1)),
                    min_size=num, max_size=num)

@st.composite
def dataset_shape(draw):
    crs = draw(DatacubeCRSStrategy)
    height = draw(st.integers(10, 400))
    width = draw(st.integers(10, 400))
    ntimes = draw(st.integers(1, 10))
    times = draw(ordered_dates(ntimes))
    return crs, height, width, times


invalid_names = ('latitude', 'longitude', 'crs', 'x', 'y')
variable_name = st.text(min_size=1, alphabet=string.ascii_letters + '_').filter(lambda n: n not in invalid_names)


@st.composite
def eo_wofs_dataset(draw):
    crs, height, width, times = draw(dataset_shape())
    arr = np.random.randint(low=0, high=255, size=(len(times), height, width), dtype='uint8')

    data_array_1 = xr.DataArray(arr,
                                dims=('time',) + crs.dimensions,
                                coords={'time': times},
                                attrs={'crs': crs})
    dataset = xr.Dataset(data_vars={'water': data_array_1},
                         attrs={'crs': crs})

    return dataset


@given(eo_wofs_dataset())
def test_wofs_stats(dataset):
    wofsstat = WofsStats()
    # Check that measurements() does something useful
    output_measurements = wofsstat.measurements([{'name': 'water'}])
    assert len(output_measurements) == 3
    expected_vars = {'count_wet', 'count_clear', 'frequency'}
    output_names = set(m['name'] for m in output_measurements)
    assert expected_vars == output_names

    # Check the computation returns something reasonable
    result = wofsstat.compute(dataset)

    assert result
    assert 'time' not in result.dims
    assert all(result_dim in dataset.dims for result_dim in result.dims)
    assert dataset.crs == result.crs

    assert set(result.data_vars) == expected_vars


@st.composite
def two_band_eo_dataset(draw):
    crs, height, width, times = draw(dataset_shape())
    arr = np.random.random_sample(size=(len(times), height, width))

    data1 = xr.DataArray(arr,
                         dims=('time',) + crs.dimensions,
                         coords={'time': times},
                         attrs={'crs': crs})
    arr = np.random.random_sample(size=(len(times), height, width))
    data2 = xr.DataArray(arr,
                         dims=('time',) + crs.dimensions,
                         coords={'time': times},
                         attrs={'crs': crs})
    name1, name2 = draw(st.tuples(variable_name, variable_name))
    dataset = xr.Dataset(data_vars={variable_name: data1, name2: data2},
                         attrs={'crs': crs})
    return dataset


@given(two_band_eo_dataset(), variable_name)
def test_normalised_difference_stats(dataset, output_name):
    var1, var2 = list(dataset.data_vars)
    ndstat = NormalisedDifferenceStats(var1, var2, output_name)
    result = ndstat.compute(dataset)

    assert result
    assert 'time' not in result.dims
    assert dataset.crs == result.crs

    expected_output_varnames = set(f'{output_name}_{stat_name}' for stat_name in ndstat.stats)
    assert set(result.data_vars) == expected_output_varnames

    # Check the measurements() function raises an error on bad input_measurements
    with pytest.raises(StatsConfigurationError):
        invalid_names = [{'name': 'foo'}]
        ndstat.measurements(invalid_names)

    # Check the measurements() function returns something reasonable
    input_measurements = [{'name': name} for name in (var1, var2)]
    output_measurements = ndstat.measurements(input_measurements)
    measurement_names = set(m['name'] for m in output_measurements)
    assert expected_output_varnames == measurement_names


@given(two_band_eo_dataset())
def test_medoid_statistic(dataset):
    medstat = Medoid()

    result = medstat.compute(dataset)
    assert result
    assert 'time' not in result.dims
    assert dataset.crs == result.crs
