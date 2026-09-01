'''Defines the basic client and methods for creating one. This client is
completely unopinionated, and provides an easy-to-use wrapper around the TD
Ameritrade HTTP API.'''

from abc import ABC, abstractmethod
from enum import Enum

import datetime
import json
import logging
import pickle
import schwab
import time
import warnings

# OrderBuilder import removed — see DEFENSE NOTE below. Order placement is
# structurally absent from this fork.

from ..utils import EnumEnforcer


def get_logger():
    return logging.getLogger(__name__)


##########################################################################
# Client

class BaseClient(EnumEnforcer):
    # This docstring will appears as documentation for __init__
    '''A basic, completely unopinionated client. This client provides the most
    direct access to the API possible. All methods return the raw response which
    was returned by the underlying API call, and the user is responsible for
    checking status codes. For methods which support responses, they can be
    found in the response object's ``json()`` method.'''

    def __init__(self, api_key, session, *, enforce_enums=True,
                 token_metadata=None):
        '''Create a new client with the given API key and session. Set
        `enforce_enums=False` to disable strict input type checking.'''
        super().__init__(enforce_enums)

        self.api_key = api_key
        self.session = session

        # Logging-related fields
        self.logger = get_logger()
        self.request_number = 0

        schwab.LOG_REDACTOR.register(api_key, 'API_KEY')

        self.token_metadata = token_metadata

        # Set the default timeout configuration
        self.set_timeout(30.0)

    # XXX: This class's tests perform monkey patching to inject synthetic values
    # of utcnow(). To avoid being confused by this, capture these values here so
    # we can use them later.
    _DATETIME = datetime.datetime
    _DATE = datetime.date

    def _log_response(self, resp, req_num):
        self.logger.debug('Req %s: GET response: %s, content=%s',
            req_num, resp.status_code, resp.text)

    def _req_num(self):
        self.request_number += 1
        return self.request_number

    def _assert_type(self, name, value, exp_types):
        value_type = type(value)
        value_type_name = '{}.{}'.format(
            value_type.__module__, value_type.__name__)
        exp_type_names = ['{}.{}'.format(
            t.__module__, t.__name__) for t in exp_types]
        if not any(isinstance(value, t) for t in exp_types):
            if len(exp_types) == 1:
                error_str = "expected type '{}' for {}, got '{}'".format(
                    exp_type_names[0], name, value_type_name)
            else:
                error_str = "expected type in ({}) for {}, got '{}'".format(
                    ', '.join(exp_type_names), name, value_type_name)
            raise ValueError(error_str)

    def _format_date_as_iso(self, var_name, dt):
        '''Formats datetime or date objects as yyyy-MM-dd'T'HH:mm:ss.SSSZ'''
        self._assert_type(var_name, dt, [self._DATE, self._DATETIME])

        if not isinstance(dt, self._DATETIME):
            dt = datetime.datetime(year=dt.year, month=dt.month, day=dt.day)

        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')

    def _format_date_as_day(self, var_name, dt):
        '''Formats datetime or date objects as YYYY-MM-DD'''
        self._assert_type(var_name, dt, [self._DATE])

        return dt.strftime('%Y-%m-%d')


    def _format_date_as_millis(self, var_name, dt):
        'Converts datetime objects to compatible millisecond values'
        self._assert_type(var_name, dt, [self._DATETIME])

        return int(dt.timestamp() * 1000)


    def set_timeout(self, timeout):
        '''Sets the timeout configuration for this client. Applies to all HTTP 
        calls.

        :param timeout: ``httpx`` timeout configuration. Passed directly to 
                        underlying ``httpx`` library. See
                        `here <https://www.python-httpx.org/advanced/
                        #setting-a-default-timeout-on-a-client>`__ for
                        examples.'''
        self.session.timeout = timeout

    def token_age(self):
        '''Get the age of the token used to create this client, in seconds. For 
        users who prefer to proactively delete their token files before the 
        become expired, this method can offer a hint for when to do so.

        Note that the actual expiration is governed by Schwab's internal 
        implementation, and the token might become expired sooner *or* later 
        than the documented seven day term.'''
        return self.token_metadata.token_age()


    ##########################################################################
    # ─────────────────────── DEFENSE NOTE (Strader fork) ────────────────────
    #
    # This fork of schwab-py has had the following methods structurally removed
    # to prevent any account or order operation from being callable:
    #
    #   Accounts:     get_account, get_account_numbers, get_accounts
    #   Orders:       get_order, cancel_order, get_orders_for_account,
    #                 get_orders_for_all_linked_accounts, place_order,
    #                 replace_order, preview_order, _make_order_query
    #   Transactions: get_transactions, get_transaction
    #   Enum classes: Account.Fields, Order.Status, Transactions.TransactionType
    #
    # Strader runs read-only: chains, quotes, bars, scanners, instruments.
    #
    # CORRECTED 2026-09-01 (st-c1af, from the st-5qjq independent audit,
    # discovery request 3). This note used to say the removed methods were
    # "unrecoverable from within this codebase". That described the method
    # table, not the capability: both clients kept generic _post_request /
    # _put_request / _delete_request methods that took an arbitrary path and
    # issued it on the authenticated session, so an order could be placed in
    # one line without touching a removed name. Those methods had zero callers
    # and are now removed too (synchronous.py, asynchronous.py). What remains
    # is _get_request, pinned to the API host.
    #
    # What this layer is, stated honestly: the fork has no write path *as
    # shipped*. The authenticated session object still exists on the client,
    # so code that constructs its own request can still transmit — this layer
    # raises the effort and the visibility of doing that, and the layers that
    # actually stop an order are the gate key, the hook, and (after stage 3)
    # execd holding the only credential. Restoring any removed method requires
    # an explicit, reviewed diff against this DEFENSE NOTE.
    #
    # Companion behavioral gate: ~/.schwab_gate_key (defense in depth).
    # ─────────────────────────────────────────────────────────────────────────
    ##########################################################################


    ##########################################################################
    # User Info and Preferences

    def get_user_preferences(self):
        '''Preferences for the logged in account, including all linked
        accounts.'''
        path = '/trader/v1/userPreference'
        return self._get_request(path, {})


    ##########################################################################
    # Quotes

    class Quote:
        class Fields(Enum):
            QUOTE = 'quote'
            FUNDAMENTAL = 'fundamental'
            EXTENDED = 'extended'
            REFERENCE = 'reference'
            REGULAR = 'regular'

    def get_quote(self, symbol, *, fields=None):
        '''
        Get quote for a symbol. Note due to limitations in URL encoding, this
        method is not recommended for instruments with symbols symbols
        containing non-alphanumeric characters, for example as futures like
        ``/ES``. To get quotes for those symbols, use :meth:`Client.get_quotes`.

        :param symbol: Single symbol to fetch
        :param fields: Fields to request. If unset, return all available data. 
                       i.e. all fields. See :class:`GetQuote.Field` for options.
        '''
        fields = self.convert_enum_iterable(fields, self.Quote.Fields)
        if fields:
            params = {'fields': ','.join(fields)}
        else:
            params = {}

        path = '/marketdata/v1/{}/quotes'.format(symbol)
        return self._get_request(path, params)

    def get_quotes(self, symbols, *, fields=None, indicative=None):
        '''Get quote for a symbol. This method supports all symbols, including
        those containing non-alphanumeric characters like ``/ES``.

        :param symbols: Iterable of symbols to fetch.
        :param fields: Fields to request. If unset, return all available data. 
                       i.e. all fields. See :class:`GetQuote.Field` for options.
        '''
        if isinstance(symbols, str):
            symbols = [symbols]

        params = {
            'symbols': ','.join(symbols)
        }

        fields = self.convert_enum_iterable(fields, self.Quote.Fields)
        if fields:
            params['fields'] = ','.join(fields)

        if indicative is not None:
            if type(indicative) is not bool:
                raise ValueError(
                        'value of \'indicative\' must be either True or False')
            params['indicative'] = 'true' if indicative else 'false'

        path = '/marketdata/v1/quotes'
        return self._get_request(path, params)


    ##########################################################################
    # Option Chains

    class Options:
        class ContractType(Enum):
            CALL = 'CALL'
            PUT = 'PUT'
            ALL = 'ALL'

        class Strategy(Enum):
            SINGLE = 'SINGLE'
            ANALYTICAL = 'ANALYTICAL'
            COVERED = 'COVERED'
            VERTICAL = 'VERTICAL'
            CALENDAR = 'CALENDAR'
            STRANGLE = 'STRANGLE'
            STRADDLE = 'STRADDLE'
            BUTTERFLY = 'BUTTERFLY'
            CONDOR = 'CONDOR'
            DIAGONAL = 'DIAGONAL'
            COLLAR = 'COLLAR'
            ROLL = 'ROLL'

        class StrikeRange(Enum):
            IN_THE_MONEY = 'ITM'
            NEAR_THE_MONEY = 'NTM'
            OUT_OF_THE_MONEY = 'OTM'
            STRIKES_ABOVE_MARKET = 'SAK'
            STRIKES_BELOW_MARKET = 'SBK'
            STRIKES_NEAR_MARKET = 'SNK'
            ALL = 'ALL'

        class Type(Enum):
            STANDARD = 'S'
            NON_STANDARD = 'NS'
            ALL = 'ALL'

        class ExpirationMonth(Enum):
            JANUARY = 'JAN'
            FEBRUARY = 'FEB'
            MARCH = 'MAR'
            APRIL = 'APR'
            MAY = 'MAY'
            JUNE = 'JUN'
            JULY = 'JUL'
            AUGUST = 'AUG'
            SEPTEMBER = 'SEP'
            OCTOBER = 'OCT'
            NOVEMBER = 'NOV'
            DECEMBER = 'DEC'
            ALL = 'ALL'

        class Entitlement(Enum):
            PAYING_PRO = 'PP'
            NON_PRO = 'NP'
            NON_PAYING_PRO = 'PN'

    def get_option_chain(
            self,
            symbol,
            *,
            contract_type=None,
            strike_count=None,
            include_underlying_quote=None,
            strategy=None,
            interval=None,
            strike=None,
            strike_range=None,
            from_date=None,
            to_date=None,
            volatility=None,
            underlying_price=None,
            interest_rate=None,
            days_to_expiration=None,
            exp_month=None,
            option_type=None,
            entitlement=None):
        '''Get option chain for an optionable Symbol.

        :param contract_type: Type of contracts to return in the chain. See
                              :class:`Options.ContractType` for choices.
        :param strike_count: The number of strikes to return above and below
                             the at-the-money price.
        :param include_underlying_quote: Include a quote for the underlying 
                                         alongside the options chain?
        :param strategy: If passed, returns a Strategy Chain. See
                        :class:`Options.Strategy` for choices.
        :param interval: Strike interval for spread strategy chains (see
                         ``strategy`` param).
        :param strike: Return options only at this strike price.
        :param strike_range: Return options for the given range. See
                             :class:`Options.StrikeRange` for choices.
        :param from_date: Only return expirations after this date. For
                          strategies, expiration refers to the nearest term
                          expiration in the strategy. Accepts ``datetime.date``.
        :param to_date: Only return expirations before this date. For
                        strategies, expiration refers to the nearest term
                        expiration in the strategy. Accepts ``datetime.date``.
        :param volatility: Volatility to use in calculations. Applies only to
                           ``ANALYTICAL`` strategy chains.
        :param underlying_price: Underlying price to use in calculations.
                                 Applies only to ``ANALYTICAL`` strategy chains.
        :param interest_rate: Interest rate to use in calculations. Applies only
                              to ``ANALYTICAL`` strategy chains.
        :param days_to_expiration: Days to expiration to use in calculations.
                                   Applies only to ``ANALYTICAL`` strategy
                                   chains
        :param exp_month: Return only options expiring in the specified month. See
                          :class:`Options.ExpirationMonth` for choices.
        :param option_type: Types of options to return. See
                            :class:`Options.Type` for choices.
        :param entitlement: Entitlement of the client.
        '''
        contract_type = self.convert_enum(
            contract_type, self.Options.ContractType)
        strategy = self.convert_enum(strategy, self.Options.Strategy)
        strike_range = self.convert_enum(
            strike_range, self.Options.StrikeRange)
        option_type = self.convert_enum(option_type, self.Options.Type)
        exp_month = self.convert_enum(exp_month, self.Options.ExpirationMonth)
        entitlement = self.convert_enum(entitlement, self.Options.Entitlement)

        params = {
            'symbol': symbol,
        }

        if contract_type is not None:
            params['contractType'] = contract_type
        if strike_count is not None:
            params['strikeCount'] = strike_count
        if include_underlying_quote is not None:
            params['includeUnderlyingQuote'] = include_underlying_quote
        if strategy is not None:
            params['strategy'] = strategy
        if interval is not None:
            params['interval'] = interval
        if strike is not None:
            params['strike'] = strike
        if strike_range is not None:
            params['range'] = strike_range
        if from_date is not None:
            params['fromDate'] = self._format_date_as_day('from_date', from_date)
        if to_date is not None:
            params['toDate'] = self._format_date_as_day('to_date', to_date)
        if volatility is not None:
            params['volatility'] = volatility
        if underlying_price is not None:
            params['underlyingPrice'] = underlying_price
        if interest_rate is not None:
            params['interestRate'] = interest_rate
        if days_to_expiration is not None:
            params['daysToExpiration'] = days_to_expiration
        if exp_month is not None:
            params['expMonth'] = exp_month
        if option_type is not None:
            params['optionType'] = option_type
        if entitlement is not None:
            params['entitlement'] = entitlement

        path = '/marketdata/v1/chains'
        return self._get_request(path, params)


    ##########################################################################
    # Option Expiration Chain

    def get_option_expiration_chain(self, symbol):
        '''Preferences for the logged in account, including all linked
        accounts.'''
        path = '/marketdata/v1/expirationchain'
        return self._get_request(path, {'symbol': symbol})

    ##########################################################################
    # Price History

    class PriceHistory:
        class PeriodType(Enum):
            DAY = 'day'
            MONTH = 'month'
            YEAR = 'year'
            YEAR_TO_DATE = 'ytd'

        class Period(Enum):
            # Daily
            ONE_DAY = 1
            TWO_DAYS = 2
            THREE_DAYS = 3
            FOUR_DAYS = 4
            FIVE_DAYS = 5
            TEN_DAYS = 10

            # Monthly
            ONE_MONTH = 1
            TWO_MONTHS = 2
            THREE_MONTHS = 3
            SIX_MONTHS = 6

            # Year
            ONE_YEAR = 1
            TWO_YEARS = 2
            THREE_YEARS = 3
            FIVE_YEARS = 5
            TEN_YEARS = 10
            FIFTEEN_YEARS = 15
            TWENTY_YEARS = 20

            # Year to date
            YEAR_TO_DATE = 1

        class FrequencyType(Enum):
            MINUTE = 'minute'
            DAILY = 'daily'
            WEEKLY = 'weekly'
            MONTHLY = 'monthly'

        class Frequency(Enum):
            # Minute
            EVERY_MINUTE = 1
            EVERY_FIVE_MINUTES = 5
            EVERY_TEN_MINUTES = 10
            EVERY_FIFTEEN_MINUTES = 15
            EVERY_THIRTY_MINUTES = 30

            # Other frequencies
            DAILY = 1
            WEEKLY = 1
            MONTHLY = 1

    def get_price_history(
            self,
            symbol,
            *,
            period_type=None,
            period=None,
            frequency_type=None,
            frequency=None,
            start_datetime=None,
            end_datetime=None,
            need_extended_hours_data=None,
            need_previous_close=None):
        '''Get price history for a symbol.

        :param period_type: The type of period to show.
        :param period: The number of periods to show. Should not be provided if
                       ``start_datetime`` and ``end_datetime``.
        :param frequency_type: The type of frequency with which a new candle
                               is formed.
        :param frequency: The number of the frequencyType to be included in each
                          candle.
        :param start_datetime: Start date.
        :param end_datetime: End date. Default is previous trading day.
        :param need_extended_hours_data: If true, return extended hours data.
                                         Default is true.
        :param need_previous_close: If true, return the previous close price and 
                                    date.
        '''
        period_type = self.convert_enum(
            period_type, self.PriceHistory.PeriodType)
        period = self.convert_enum(period, self.PriceHistory.Period)
        frequency_type = self.convert_enum(
            frequency_type, self.PriceHistory.FrequencyType)
        frequency = self.convert_enum(
            frequency, self.PriceHistory.Frequency)

        params = {
                'symbol': symbol,
        }

        if period_type is not None:
            params['periodType'] = period_type
        if period is not None:
            params['period'] = period
        if frequency_type is not None:
            params['frequencyType'] = frequency_type
        if frequency is not None:
            params['frequency'] = frequency
        if start_datetime is not None:
            params['startDate'] = self._format_date_as_millis(
                'start_datetime', start_datetime)
        if end_datetime is not None:
            params['endDate'] = self._format_date_as_millis(
                'end_datetime', end_datetime)
        if need_extended_hours_data is not None:
            params['needExtendedHoursData'] = need_extended_hours_data
        if need_previous_close is not None:
            params['needPreviousClose'] = need_previous_close

        path = '/marketdata/v1/pricehistory'.format(symbol)
        return self._get_request(path, params)


    ##########################################################################
    # Price history utilities

    def __normalize_start_and_end_datetimes(self, start_datetime, end_datetime):
        if start_datetime is None:
            start_datetime = datetime.datetime(year=1971, month=1, day=1)
        if end_datetime is None:
            end_datetime = (datetime.datetime.utcnow() +
                    datetime.timedelta(days=7))

        return start_datetime, end_datetime


    def get_price_history_every_minute(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a per-minute
        granularity. This endpoint currently appears to return up to 48 days of
        data.
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.DAY,
                period=self.PriceHistory.Period.ONE_DAY,
                frequency_type=self.PriceHistory.FrequencyType.MINUTE,
                frequency=self.PriceHistory.Frequency.EVERY_MINUTE,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_five_minutes(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a per-five-minutes
        granularity. This endpoint currently appears to return approximately
        nine months of data.
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.DAY,
                period=self.PriceHistory.Period.ONE_DAY,
                frequency_type=self.PriceHistory.FrequencyType.MINUTE,
                frequency=self.PriceHistory.Frequency.EVERY_FIVE_MINUTES,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_ten_minutes(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a per-ten-minutes
        granularity. This endpoint currently appears to return approximately
        nine months of data.
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.DAY,
                period=self.PriceHistory.Period.ONE_DAY,
                frequency_type=self.PriceHistory.FrequencyType.MINUTE,
                frequency=self.PriceHistory.Frequency.EVERY_TEN_MINUTES,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_fifteen_minutes(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a per-fifteen-minutes
        granularity. This endpoint currently appears to return approximately
        nine months of data.
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.DAY,
                period=self.PriceHistory.Period.ONE_DAY,
                frequency_type=self.PriceHistory.FrequencyType.MINUTE,
                frequency=self.PriceHistory.Frequency.EVERY_FIFTEEN_MINUTES,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_thirty_minutes(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a per-thirty-minutes
        granularity. This endpoint currently appears to return approximately
        nine months of data.
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.DAY,
                period=self.PriceHistory.Period.ONE_DAY,
                frequency_type=self.PriceHistory.FrequencyType.MINUTE,
                frequency=self.PriceHistory.Frequency.EVERY_THIRTY_MINUTES,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_day(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a daily granularity. 
        The exact period of time over which this endpoint returns data is 
        unclear, although it has been observed returning data as far back as 
        1985 (for ``AAPL``).
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.YEAR,
                period=self.PriceHistory.Period.TWENTY_YEARS,
                frequency_type=self.PriceHistory.FrequencyType.DAILY,
                frequency=self.PriceHistory.Frequency.EVERY_MINUTE,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    def get_price_history_every_week(
            self, symbol, *, start_datetime=None, end_datetime=None, 
            need_extended_hours_data=None, need_previous_close=None):
        '''
        Fetch price history for a stock or ETF symbol at a weekly granularity.
        The exact period of time over which this endpoint returns data is 
        unclear, although it has been observed returning data as far back as 
        1985 (for ``AAPL``).
        '''

        start_datetime, end_datetime = self.__normalize_start_and_end_datetimes(
                start_datetime, end_datetime)

        return self.get_price_history(
                symbol,
                period_type=self.PriceHistory.PeriodType.YEAR,
                period=self.PriceHistory.Period.TWENTY_YEARS,
                frequency_type=self.PriceHistory.FrequencyType.WEEKLY,
                frequency=self.PriceHistory.Frequency.EVERY_MINUTE,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
                need_extended_hours_data=need_extended_hours_data, 
                need_previous_close=need_previous_close)


    ##########################################################################
    # Movers

    class Movers:
        class Index(Enum):
            DJI = '$DJI'
            COMPX = '$COMPX'
            SPX = '$SPX'
            NYSE = 'NYSE'
            NASDAQ = 'NASDAQ'
            OTCBB = 'OTCBB'
            INDEX_ALL = 'INDEX_ALL'
            EQUITY_ALL = 'EQUITY_ALL'
            OPTION_ALL = 'OPTION_ALL'
            OPTION_PUT = 'OPTION_PUT'
            OPTION_CALL = 'OPTION_CALL'

        class SortOrder(Enum):
            '''Sort by a particular attribute'''
            VOLUME = 'VOLUME'
            TRADES = 'TRADES'
            PERCENT_CHANGE_UP = 'PERCENT_CHANGE_UP'
            PERCENT_CHANGE_DOWN = 'PERCENT_CHANGE_DOWN'

        class Frequency(Enum):
            '''To return movers with the specified directions of up or down'''
            ZERO = 0
            ONE = 1
            FIVE = 5
            TEN = 10
            THIRTY = 30
            SIXTY = 60
        

    def get_movers(self, index, *, sort_order=None, frequency=None):
        '''Get a list of the top ten movers for a given index.

        :param index: Category of mover. See :class:`Movers.Index` for valid 
                      values.
        :param sort_order: Order in which to return values. See 
                           :class:`Movers.SortOrder for valid values`
        :param frequency: Only return movers that saw this magnitude or greater. 
                          See :class:`Movers.Frequency` for valid values.
        '''
        index = self.convert_enum(index, self.Movers.Index)
        sort_order = self.convert_enum(sort_order, self.Movers.SortOrder)
        frequency = self.convert_enum(frequency, self.Movers.Frequency)

        path = '/marketdata/v1/movers/{}'.format(index)

        params = {}
        if sort_order is not None:
            params['sort'] = sort_order
        if frequency is not None:
            params['frequency'] = str(frequency)

        return self._get_request(path, params)


    ##########################################################################
    # Market Hours

    class MarketHours:
        class Market(Enum):
            EQUITY = 'equity'
            OPTION = 'option'
            BOND = 'bond'
            FUTURE = 'future'
            FOREX = 'forex'

    def get_market_hours(self, markets, *, date=None):
        '''Retrieve market hours for specified markets

        :param markets: Markets for which to return trading hours.
        :param date: Date for which to return market hours. Accepts values up to 
                     one year from today. Accepts ``datetime.date``.
        '''
        markets = self.convert_enum_iterable(markets, self.MarketHours.Market)

        params = {
                'markets': ','.join(markets)
        }
        if date is not None:
            params['date'] = self._format_date_as_day('date', date)

        return self._get_request('/marketdata/v1/markets', params)


    ##########################################################################
    # Instrument

    class Instrument:
        class Projection(Enum):
            SYMBOL_SEARCH = 'symbol-search'
            SYMBOL_REGEX = 'symbol-regex'
            DESCRIPTION_SEARCH = 'desc-search'
            DESCRIPTION_REGEX = 'desc-regex'
            SEARCH = 'search'
            FUNDAMENTAL = 'fundamental'

    def get_instruments(self, symbols, projection):
        '''Get instrument details by using different search methods. Also used 
        to get fundamental instrument data by use of the ``FUNDAMENTAL`` 
        projection.

        :param symbol: For ``FUNDAMENTAL`` projection, the symbol for which to 
                       get fundamentals. For other projections, a search term. 
                       See below for details.
        :param projection: Search mode, or ``FUNDAMENTAL`` for instrument 
                           fundamentals. See :class:`Instrument.Projection`.

        This method is a bit of a chimera because it supports both symbol 
        search and fundamentals lookup. The format and interpretation of the 
        ``symbol`` parameter differs according ot the value of the 
        ``projection`` parameter:

        .. list-table::
           :widths: 25 25 50
           :header-rows: 1

           * - ``projection`` value
             - Accepted values of ``symbol``
             - ``symbol`` interpretation
           * - ``SYMBOL_SEARCH``
             - String, or array of strings
             - Symbols for which to search results
           * - ``SYMBOL_REGEX``
             - Single string
             - Regular expression to match against symbols
           * - ``DESCRIPTION_SEARCH``
             - Single string
             - String to search for in symbol description
           * - ``DESCRIPTION_REGEX``
             - Single string
             - Regex to search for in symbol description
           * - ``SEARCH``
             - Single string
             - Regular expression to match against symbols
           * - ``FUNDAMENTAL``
             - String, or array of strings
             - Symbols for which to return fundamentals. Exact match.
        '''
        if isinstance(symbols, str):
            symbols = [symbols]

        projection = self.convert_enum(projection, self.Instrument.Projection)

        params = {
                'symbol': ','.join(symbols),
                'projection': projection,
        }

        return self._get_request('/marketdata/v1/instruments', params)


    def get_instrument_by_cusip(self, cusip):
        '''Get instrument information for a single instrument by CUSIP.

        :param cusip: String representing CUSIP of instrument for which to fetch 
                      data. Note leading zeroes must be preserved.
        '''
        if not isinstance(cusip, str):
            raise ValueError('cusip must be passed as str')

        return self._get_request(
                '/marketdata/v1/instruments/{}'.format(cusip), {})
