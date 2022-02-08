""" Fitting of various models. """

import operator
import warnings
from typing import Any, Dict, Tuple

import matplotlib.pyplot as plt
import numpy as np
import scipy
from lmfit.model import Model, ModelResult
from qcodes.data.data_array import DataArray

import qtt.pgeometry
from qtt.algorithms.functions import Fermi, FermiLinear, estimate_dominant_frequency, gaussian, linear_function, sine


def extract_lmfit_parameters(lmfit_model: Model, lmfit_result: ModelResult) -> Dict[str, Any]:
    """ Convert lmfit results to a dictionary

    Args:
        lmfit_model: Model that was fitted
        lmfit_result: Fitting results of lmfit

    Returns:
        Dictionary with fitting parameters
    """
    param_names = lmfit_model.param_names
    fitted_parameters = np.array([lmfit_result.best_values[p] for p in param_names])
    initial_parameters = np.array([lmfit_result.init_params[p] for p in param_names])

    if lmfit_result.covar is None:
        fitted_parameters_covariance = None
    else:
        fitted_parameters_covariance = np.diag(lmfit_result.covar)

    results = {'fitted_parameters': fitted_parameters, 'initial_parameters': initial_parameters,
               'reduced_chi_squared': lmfit_result.redchi, 'type': lmfit_model.name,
               'fitted_parameter_dictionary': lmfit_result.best_values,
               'fitted_parameters_covariance': fitted_parameters_covariance}
    return results


def _integral(x_data, y_data):
    """ Calculate integral of function """
    d_xdata = np.diff(x_data)
    d_xdata = np.hstack([d_xdata, d_xdata[-1]])
    data_integral = np.sum(d_xdata * y_data)
    return data_integral


def _estimate_double_gaussian_parameters(x_data, y_data, fast_estimate=False):
    """ Estimate of double gaussian model parameters."""
    maxsignal = np.percentile(x_data, 98)
    minsignal = np.percentile(x_data, 2)

    data_left = y_data[:int(len(y_data) / 2)]
    data_right = y_data[int(len(y_data) / 2):]

    amplitude_left = np.max(data_left)
    amplitude_right = np.max(data_right)

    if fast_estimate:
        sigma_left = (maxsignal - minsignal) * 1 / 20
        sigma_right = (maxsignal - minsignal) * 1 / 20
        alpha = .1
        mean_left = minsignal + (alpha) * (maxsignal - minsignal)
        mean_right = minsignal + (1 - alpha) * (maxsignal - minsignal)
    else:
        x_data_left = x_data[:int(len(y_data) / 2)]
        x_data_right = x_data[int(len(y_data) / 2):]

        data_integral_left = _integral(x_data_left, data_left)
        data_integral_right = _integral(x_data_right, data_right)

        sigma_left = data_integral_left / (np.sqrt(2 * np.pi) * amplitude_left)
        sigma_right = data_integral_right / (np.sqrt(2 * np.pi) * amplitude_right)

        mean_left = np.sum(x_data_left * data_left) / np.sum(data_left)
        mean_right = np.sum(x_data_right * data_right) / np.sum(data_right)
    initial_params = np.array([amplitude_left, amplitude_right, sigma_left, sigma_right, mean_left, mean_right])
    return initial_params


def fit_double_gaussian(x_data, y_data, maxiter=None, maxfun=None, verbose=1, initial_params=None):
    """ Fitting of double gaussian

    Fitting the Gaussians and finding the split between the up and the down state,
    separation between the max of the two gaussians measured in the sum of the std.

    Args:
        x_data (array): x values of the data
        y_data (array): y values of the data
        maxiter (int): Legacy argument, not used any more
        maxfun (int): Legacy argument, not used any more
        verbose (int): set to >0 to print convergence messages
        initial_params (None or array): optional, initial guess for the fit parameters:
            [A_dn, A_up, sigma_dn, sigma_up, mean_dn, mean_up]

    Returns:
        par_fit (array): fit parameters of the double gaussian: [A_dn, A_up, sigma_dn, sigma_up, mean_dn, mean_up]
        result_dict (dict): dictionary with results of the fit. Fields guaranteed in the dictionary:
            parameters (array): Fitted parameters
            parameters initial guess (array): initial guess for the fit parameters, either the ones give to the
                function, or generated by the function: [A_dn, A_up, sigma_dn, sigma_up, mean_dn, mean_up]
            reduced_chi_squared (float): Reduced chi squared value of the fit
            separation (float): separation between the max of the two gaussians measured in the sum of the std
            split (float): value that separates the up and the down level
            left (array), right (array): Parameters of the left and right fitted Gaussian

    """
    if maxiter is not None:
        warnings.warn('argument maxiter is not used any more')
    if maxfun is not None:
        warnings.warn('argument maxfun is not used any more')

    if initial_params is None:
        initial_params = _estimate_double_gaussian_parameters(x_data, y_data)

    def _double_gaussian(x, A_dn, A_up, sigma_dn, sigma_up, mean_dn, mean_up):
        """ Double Gaussian helper function for lmfit """
        gauss_dn = gaussian(x, mean_dn, sigma_dn, A_dn)
        gauss_up = gaussian(x, mean_up, sigma_up, A_up)
        double_gauss = gauss_dn + gauss_up
        return double_gauss

    lmfit_method = 'least_squares'
    double_gaussian_model = Model(_double_gaussian)
    delta_x = x_data.max() - x_data.min()
    bounds = [x_data.min() - .1 * delta_x, x_data.max() + .1 * delta_x]
    double_gaussian_model.set_param_hint('mean_up', min=bounds[0], max=bounds[1])
    double_gaussian_model.set_param_hint('mean_dn', min=bounds[0], max=bounds[1])
    double_gaussian_model.set_param_hint('A_up', min=0)
    double_gaussian_model.set_param_hint('A_dn', min=0)

    param_names = double_gaussian_model.param_names
    result = double_gaussian_model.fit(y_data, x=x_data, **dict(zip(param_names, initial_params)), verbose=False,
                                       method=lmfit_method)

    par_fit = np.array([result.best_values[p] for p in param_names])

    if par_fit[4] > par_fit[5]:
        par_fit = np.take(par_fit, [1, 0, 3, 2, 5, 4])
    # separation is the difference between the max of the gaussians divided by the sum of the std of both gaussians
    separation = (par_fit[5] - par_fit[4]) / (abs(par_fit[2]) + abs(par_fit[3]))
    # split equal distant to both peaks measured in std from the peak
    weigthed_distance_split = par_fit[4] + separation * abs(par_fit[2])

    result_dict = {'parameters': par_fit, 'parameters initial guess': initial_params, 'separation': separation,
                   'split': weigthed_distance_split, 'reduced_chi_squared': result.redchi,
                   'left': np.take(par_fit, [4, 2, 0]), 'right': np.take(par_fit, [5, 3, 1]),
                   'type': 'fitted double gaussian'}

    return par_fit, result_dict


def _double_gaussian_parameters(gauss_left, gauss_right):
    return np.vstack((gauss_left[::-1], gauss_right[::-1])).T.flatten()


def refit_double_gaussian(result_dict, x_data, y_data, gaussian_amplitude_ratio_threshold=8):
    """ Improve fit of double Gaussian by estimating the initial parameters based on an existing fit

    Args:
        result_dict(dict): Result dictionary from fit_double_gaussian
        x_data (array): Independent data
        y_data (array): Signal data
        gaussian_amplitude_ratio_threshold (float): If ratio between amplitudes of Gaussian peaks is larger than
                            this fit, re-estimate
    Returns:
        Dictionary with improved fitting results
    """

    mean = operator.itemgetter(0)
    std = operator.itemgetter(1)
    amplitude = operator.itemgetter(2)

    if amplitude(result_dict['left']) > amplitude(result_dict['right']):
        large_gaussian_parameters = result_dict['left']
        small_gaussian_parameters = result_dict['right']
    else:
        large_gaussian_parameters = result_dict['right']
        small_gaussian_parameters = result_dict['left']
    gaussian_ratio = amplitude(large_gaussian_parameters) / amplitude(small_gaussian_parameters)

    if gaussian_ratio > gaussian_amplitude_ratio_threshold:
        # re-estimate by fitting a single gaussian to the data remaining after removing the main gaussian
        y_residual = y_data - gaussian(x_data, *large_gaussian_parameters)
        idx = np.logical_and(x_data > mean(large_gaussian_parameters) - 1.5 * std(large_gaussian_parameters),
                             x_data < mean(large_gaussian_parameters) + 1.5 * std(large_gaussian_parameters))
        y_residual[idx] = 0
        gauss_fit, _ = fit_gaussian(x_data, y_residual)

        initial_parameters = _double_gaussian_parameters(large_gaussian_parameters, gauss_fit[:3])
        _, result_dict_refit = fit_double_gaussian(x_data, y_data, initial_params=initial_parameters)
        if result_dict_refit['reduced_chi_squared'] < result_dict['reduced_chi_squared']:
            result_dict = result_dict_refit
    return result_dict


def _estimate_initial_parameters_gaussian(x_data, y_data, include_offset):
    maxsignal = np.percentile(x_data, 98)
    minsignal = np.percentile(x_data, 2)
    amplitude = np.max(y_data) - np.min(y_data)
    s = (maxsignal - minsignal) * 1 / 20
    mean = x_data[int(np.where(y_data == np.max(y_data))[0][0])]
    offset = np.min(y_data)
    if include_offset:
        initial_parameters = np.array([mean, s, amplitude, offset])
    else:
        initial_parameters = np.array([mean, s, amplitude])
    return initial_parameters


def fit_gaussian(x_data, y_data, maxiter=None, maxfun=None, verbose=0, initial_parameters=None, initial_params=None,
                 estimate_offset=True):
    """ Fitting of a gaussian, see function 'gaussian' for the model that is fitted

    The final optimization of the fit is performed with `lmfit <https://lmfit.github.io/lmfit-py/>`
    using the `least_squares` method.

    Args:
        x_data (array): x values of the data
        y_data (array): y values of the data
        verbose (int): set positive for verbose fit
        initial_parameters (None or array): optional, initial guess for the
            fit parameters: [mean, s, amplitude, offset]
        estimate_offset (bool): If True then include offset in the Gaussian parameters

        maxiter (int): Legacy argument, not used
        maxfun (int): Legacy argument, not used

    Returns:
        par_fit (array): fit parameters of the gaussian: [mean, s, amplitude, offset]
        result_dict (dict): result dictonary containging the fitparameters and the initial guess parameters
    """

    if initial_params is not None:
        warnings.warn('use initial_parameters instead of initial_params')
        initial_parameters = initial_params
    if maxiter is not None:
        warnings.warn('argument maxiter is not used any more')
    if maxfun is not None:
        warnings.warn('argument maxfun is not used any more')

    if initial_parameters is None:
        initial_parameters = _estimate_initial_parameters_gaussian(x_data, y_data, include_offset=estimate_offset)

    if estimate_offset:
        def gaussian_model(x, mean, sigma, amplitude, offset):
            """ Gaussian helper function for lmfit """
            y = gaussian(x, mean, sigma, amplitude, offset)
            return y
    else:
        def gaussian_model(x, mean, sigma, amplitude):  # type: ignore
            """ Gaussian helper function for lmfit """
            y = gaussian(x, mean, sigma, amplitude)
            return y

    lmfit_method = 'least_squares'
    lmfit_model = Model(gaussian_model)
    lmfit_model.set_param_hint('amplitude', min=0)
    lmfit_result = lmfit_model.fit(y_data, x=x_data, **dict(zip(lmfit_model.param_names, initial_parameters)),
                                   verbose=verbose, method=lmfit_method)
    result_dict = extract_lmfit_parameters(lmfit_model, lmfit_result)

    result_dict['parameters fitted gaussian'] = result_dict['fitted_parameters']
    result_dict['parameters initial guess'] = result_dict['initial_parameters']

    return result_dict['fitted_parameters'], result_dict


def fit_sine(x_data: np.ndarray, y_data: np.ndarray, initial_parameters=None,
             positive_amplitude=True) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """ Fit a sine wave for the inputted data; see sine function in functions.py for model

    Args:
        x_data: x data points
        y_data: data to be fitted
        initial_parameters: list of 4 floats with initial guesses for: amplitude, frequency, phase and offset
        positive_amplitude: If True, then enforce the amplitude to be positive
    Returns:
        result_dict
    """
    if initial_parameters is None:
        initial_parameters = _estimate_initial_parameters_sine(x_data, y_data)

    lmfit_model = Model(sine)
    if positive_amplitude:
        lmfit_model.set_param_hint('amplitude', min=0)
    lmfit_result = lmfit_model.fit(y_data, x=x_data, **dict(zip(lmfit_model.param_names,
                                   initial_parameters)), method='least_squares')
    result_dict = extract_lmfit_parameters(lmfit_model, lmfit_result)

    return result_dict['fitted_parameters'], result_dict


def _estimate_initial_parameters_sine(x_data: np.ndarray, y_data: np.ndarray) -> np.ndarray:
    amplitude = (np.max(y_data) - np.min(y_data)) / 2
    offset = np.mean(y_data)
    frequency = estimate_dominant_frequency(y_data, sample_rate=1 / np.mean(np.diff(x_data)), remove_dc=True, fig=None)
    phase = np.pi/2 - 2*np.pi*frequency*x_data[np.argmax(y_data)]
    initial_parameters = np.array([amplitude, frequency, phase, offset])
    return initial_parameters


def _estimate_fermi_model_center_amplitude(x_data, y_data_linearized, fig=None):
    """ Estimates the following properties of a charge addition line; the center location
        of the addition line. The amplitude step size caused by the addition line.

    Args:
        x_data (1D array): The independent data.
        y_data_linearized (1D array): The dependent data with linear estimate subtracted.

    Returns:
        xdata_center_est (float): Estimate of x-data value at the center.
        amplitude_step (float): Estimate of the amplitude of the step.
    """
    sigma = x_data.size / 250
    y_derivative_filtered = scipy.ndimage.gaussian_filter(y_data_linearized, sigma, order=1)

    # assume step is steeper than overall slope
    estimated_index = np.argmax(np.abs(y_derivative_filtered))
    center_index = int(x_data.size / 2)

    # prevent guess to be at the edges
    if estimated_index < 0.01 * x_data.size or estimated_index > 0.99 * x_data.size:
        estimated_center_xdata = np.mean(x_data)
    else:
        estimated_center_xdata = x_data[estimated_index]

    split_offset = int(np.floor(x_data.size / 10))
    mean_right = np.mean(y_data_linearized[(center_index + split_offset):])
    mean_left = np.mean(y_data_linearized[:(center_index - split_offset)])
    amplitude_step = -(mean_right - mean_left)

    if np.sign(-y_derivative_filtered[estimated_index]) != np.sign(amplitude_step):
        warnings.warn('step size might be incorrect')

    if fig is not None:
        _plot_fermi_model_estimate(x_data, y_data_linearized, estimated_center_xdata,
                                   amplitude_step, estimated_index, fig=fig)

    return estimated_center_xdata, amplitude_step


def _plot_fermi_model_estimate(x_data, y_data_linearized, estimated_center_xdata, amplitude_step, estimated_index, fig):
    T = np.std(x_data) / 100
    fermi_parameters = [estimated_center_xdata, amplitude_step, T]

    plt.figure(fig)
    plt.clf()
    plt.plot(x_data, y_data_linearized, '.b', label='y_data_linearized')
    plt.plot(x_data, Fermi(x_data, *fermi_parameters), '-c', label='estimated model')
    plt.plot(x_data[estimated_index], y_data_linearized[estimated_index], '.g', label='max slope')
    vline = plt.axvline(estimated_center_xdata, label='estimated_center_xdata')
    vline.set_color('c')
    vline.set_alpha(.5)
    plt.legend()


def initFermiLinear(x_data, y_data, fig=None):
    """ Initialization of fitting a FermiLinear function.

    First the linear part is estimated, then the Fermi part of the function.

    Args:
        x_data (array): data for independent variable
        y_data (array): dependent variable
        fig (int) : figure number

    Returns:
        linear_part (array)
        fermi_part (array)
    """
    xdata = np.array(x_data)
    ydata = np.array(y_data)
    n = xdata.size
    nx = int(np.ceil(n / 5))

    if nx < 4:
        p1, _ = scipy.optimize.curve_fit(linear_function, np.array(xdata[0:100]),
                                         np.array(ydata[0:100]))

        a = p1[0]
        b = p1[1]
        linear_part = [a, b]
        ylin = linear_function(xdata, linear_part[0], linear_part[1])
        cc = np.mean(xdata)
        A = 0
        T = np.std(xdata) / 10
        fermi_part = [cc, A, T]
    else:
        # guess initial linear part
        mx = np.mean(xdata)
        my = np.mean(ydata)
        dx = np.hstack((np.diff(xdata[0:nx]), np.diff(xdata[-nx:])))
        dx = np.mean(dx)
        dd = np.hstack((np.diff(ydata[0:nx]), np.diff(ydata[-nx:])))
        dd = np.convolve(dd, np.array([1., 1, 1]) / 3)  # smooth
        if dd.size > 15:
            dd = np.array(sorted(dd))
            w = int(dd.size / 10)
            a = np.mean(dd[w:-w]) / dx
        else:
            a = np.mean(dd) / dx
        b = my - a * mx
        linear_part = [a, b]
        ylin = linear_function(xdata, *linear_part)

        # subtract linear part
        yr = ydata - ylin

        cc, A = _estimate_fermi_model_center_amplitude(xdata, yr)

        T = np.std(xdata) / 100
        linear_part[1] = linear_part[1] - A / 2  # correction
        fermi_part = [cc, A, T]

        yr = ydata - linear_function(xdata, *linear_part)

    if fig is not None:
        yf = FermiLinear(xdata, *linear_part, *fermi_part)

        xx = np.hstack((xdata[0:nx], xdata[-nx:]))
        yy = np.hstack((ydata[0:nx], ydata[-nx:]))
        plt.figure(fig)
        plt.clf()
        plt.plot(xdata, ydata, '.b', label='raw data')
        plt.plot(xx, yy, 'ok')
        qtt.pgeometry.plot2Dline([-1, 0, cc], ':c', label='center')
        plt.plot(xdata, ylin, '-c', alpha=.5, label='fitted linear function')
        plt.plot(xdata, yf, '-m', label='fitted FermiLinear function')

        plt.title('initFermiLinear', fontsize=12)
        plt.legend(numpoints=1)

        plt.figure(fig + 1)
        plt.clf()
        # TODO: When nx < 4 and fig is not None -> yr is undefined
        plt.plot(xdata, yr, '.b', label='Fermi part')
        fermi_part_values = Fermi(xdata, cc, A, T)
        plt.plot(xdata, fermi_part_values, '-m', label='initial estimate')
        plt.legend()
    return linear_part, fermi_part


# %%

def fitFermiLinear(x_data, y_data, verbose=0, fig=None, lever_arm=1.16, l=None, use_lmfit=False):
    """ Fit data to a Fermi-Linear function

    Args:
        x_data (array): independent variable data
        y_data (array): dependent variable data
        verbose (int) : verbosity (0 == silent). Not used
        fig (int) : figure number
        lever_arm (float): leverarm passed to FermiLinear function
        l (Any): Deprecated parameter. Use lever_arm instead
        use_lmfit (bool): If True use lmfit for optimization, otherwise use scipy

    Returns:
        p (array): fitted function parameters
        results (dict): extra fitting data

    .. seealso:: FermiLinear
    """
    x_data = np.array(x_data)
    y_data = np.array(y_data)

    if l is not None:
        warnings.warn('use argument lever_arm instead of l')
        lever_arm = l

    # initial values
    linear_part, fermi_part = initFermiLinear(x_data, y_data, fig=None)
    initial_parameters = linear_part + fermi_part

    def fermi_linear_fitting_function(x_data, a, b, cc, A, T):
        return FermiLinear(x_data, a, b, cc, A, T, l=lever_arm)

    if use_lmfit:
        import lmfit

        gmodel = lmfit.Model(fermi_linear_fitting_function)
        param_init = dict(zip(gmodel.param_names, initial_parameters))
        gmodel.set_param_hint('T', min=0)

        params = gmodel.make_params(**param_init)
        lmfit_results = gmodel.fit(y_data, params, x_data=x_data)
        fitting_results = lmfit_results.fit_report()
        fitted_parameters = np.array([lmfit_results.best_values[p] for p in gmodel.param_names])
    else:
        fitting_results = scipy.optimize.curve_fit(
            fermi_linear_fitting_function, x_data, y_data, p0=initial_parameters)
        fitted_parameters = fitting_results[0]

    results = dict({'fitted_parameters': fitted_parameters, 'pp': fitting_results,
                    'centre': fitted_parameters[2], 'initial_parameters': initial_parameters, 'lever_arm': lever_arm,
                    'fitting_results': fitting_results})

    if fig is not None:
        plot_FermiLinear(x_data, y_data, results, fig=fig)

    return fitted_parameters, results


def plot_FermiLinear(x_data, y_data, results, fig=10):
    """ Plot results for fitFermiLinear

    Args:
        x_data (np.array): Independant variable
        y_data (np.array): Dependant variable
        results (dict): Output of fitFermiLinear
        fig (int): Figure handle

    """
    fitted_parameters = results['fitted_parameters']
    lever_arm = results['lever_arm']
    y = FermiLinear(x_data, *list(fitted_parameters), l=lever_arm)

    plt.figure(fig)
    plt.clf()
    plt.plot(x_data, y_data, '.b', label='data')
    plt.plot(x_data, y, 'm-', label='fitted FermiLinear')
    plt.legend(numpoints=1)

# %%


def fit_addition_line_array(x_data, y_data, trimborder=True):
    """ Fits a FermiLinear function to the addition line and finds the middle of the step.

    Note: Similar to fit_addition_line but directly works with arrays of data.

    Args:
        x_data (array): independent variable data
        y_data (array): dependent variable data
        trimborder (bool): determines if the edges of the data are taken into account for the fit

    Returns:
        m_addition_line (float): x value of the middle of the addition line
        pfit (array): fit parameters of the Fermi Linear function
        pguess (array): parameters of initial guess
    """
    if trimborder:
        cut_index = max(min(int(x_data.size / 40), 100), 1)
        x_data = x_data[cut_index: -cut_index]
        y_data = y_data[cut_index: -cut_index]

    fit_parameters, extra_data = fitFermiLinear(x_data, y_data, verbose=1, fig=None)
    initial_parameters = extra_data['p0']
    m_addition_line = fit_parameters[2]

    return m_addition_line, {'fit parameters': fit_parameters, 'parameters initial guess': initial_parameters}


def fit_addition_line(dataset, trimborder=True):
    """Fits a FermiLinear function to the addition line and finds the middle of the step.

    Args:
        dataset (qcodes dataset): The 1d measured data of addition line.
        trimborder (bool): determines if the edges of the data are taken into account for the fit.

    Returns:
        m_addition_line (float): x value of the middle of the addition line
        result_dict (dict): dictionary with the following results:
            fit parameters (array): fit parameters of the Fermi Linear function
            parameters initial guess (array): parameters of initial guess
            dataset fit (qcodes dataset): dataset with fitted Fermi Linear function
            dataset initial guess (qcodes dataset):dataset with guessed Fermi Linear function

    See also: FermiLinear and fitFermiLinear
    """
    y_array = dataset.default_parameter_array()
    setarray = y_array.set_arrays[0]
    x_data = np.array(setarray)
    y_data = np.array(y_array)

    if trimborder:
        cut_index = max(min(int(x_data.size / 40), 100), 1)
        x_data = x_data[cut_index: -cut_index]
        y_data = y_data[cut_index: -cut_index]
        setarray = setarray[cut_index: -cut_index]

    m_addition_line, result_dict = fit_addition_line_array(x_data, y_data, trimborder=False)

    y_initial_guess = FermiLinear(x_data, *list(result_dict['parameters initial guess']))
    dataset_guess = DataArray(name='fit', label='fit', preset_data=y_initial_guess, set_arrays=(setarray,))

    y_fit = FermiLinear(x_data, *list(result_dict['fit parameters']))
    dataset_fit = DataArray(name='fit', label='fit', preset_data=y_fit, set_arrays=(setarray,))

    return m_addition_line, {'dataset fit': dataset_fit, 'dataset initial guess': dataset_guess}
