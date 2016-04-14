# -*- coding: UTF-8 -*-
# File: group.py
# Author: Yuxin Wu <ppwwyyxx@gmail.com>

import tensorflow as tf
from contextlib import contextmanager
import time

from .base import Callback, TrainCallbackType, TestCallbackType
from .summary import *
from ..utils import *

__all__ = ['Callbacks']

@contextmanager
def create_test_graph(trainer):
    model = trainer.model
    with tf.Graph().as_default() as Gtest:
        # create a global step var in test graph
        global_step_var = tf.Variable(
            0, trainable=False, name=GLOBAL_STEP_OP_NAME)
        input_vars = model.get_input_vars()
        cost = model.get_cost(input_vars, is_training=False)
        yield Gtest

@contextmanager
def create_test_session(trainer):
    """ create a test-time session from trainer"""
    with create_test_graph(trainer):
        with tf.Session() as sess:
            yield sess

class CallbackTimeLogger(object):
    def __init__(self):
        self.times = []
        self.tot = 0

    def add(self, name, time):
        self.tot += time
        self.times.append((name, time))

    @contextmanager
    def timed_callback(self, name):
        s = time.time()
        yield
        self.add(name, time.time() - s)

    def log(self):
        """ log the time of some heavy callbacks """
        if self.tot < 3:
            return
        msgs = []
        for name, t in self.times:
            if t / self.tot > 0.3 and t > 1:
                msgs.append("{}:{:.3f}sec".format(name, t))
        logger.info(
            "Callbacks took {:.3f} sec in total. {}".format(
                self.tot, ' '.join(msgs)))

class TestCallbackContext(object):
    """
    A class holding the context needed for running TestCallback
    """
    def __init__(self):
        self.sess = None

    @contextmanager
    def before_train_context(self, trainer):
        if self.sess is None:
            with create_test_session(trainer) as sess:
                self.sess = sess
                self.graph = sess.graph
                self.saver = tf.train.Saver()
        with self.graph.as_default(), self.sess.as_default():
            yield

    # TODO also do this for after_train?

    def restore_checkpoint(self):
        ckpt = tf.train.get_checkpoint_state(logger.LOG_DIR)
        if ckpt is None:
            raise RuntimeError(
                "Cannot find a checkpoint state. Do you forget to use ModelSaver before all TestCallback?")
        logger.info(
            "Restore checkpoint from {}".format(ckpt.model_checkpoint_path))
        self.saver.restore(self.sess, ckpt.model_checkpoint_path)

    @contextmanager
    def trigger_epoch_context(self):
        with self.graph.as_default(), self.sess.as_default():
            yield

class Callbacks(Callback):
    """
    A container to hold all callbacks, and execute them in the right order and proper session.
    """
    def __init__(self, cbs):
        """
        :param cbs: a list of `Callbacks`
        """
        # check type
        for cb in cbs:
            assert isinstance(cb, Callback), cb.__class__
            if not isinstance(cb.type, (TrainCallbackType, TestCallbackType)):
                raise ValueError(
                    "Unknown callback running graph {}!".format(str(cb.type)))

        self.cbs = cbs
        self.test_callback_context = TestCallbackContext()

    def _before_train(self):
        for cb in self.cbs:
            if isinstance(cb.type, TrainCallbackType):
                cb.before_train(self.trainer)
            else:
                with self.test_callback_context.before_train_context(self.trainer):
                    cb.before_train(self.trainer)

    def _after_train(self):
        for cb in self.cbs:
            cb.after_train()

    def trigger_step(self):
        for cb in self.cbs:
            if isinstance(cb.type, TrainCallbackType):
                cb.trigger_step()
        # test callback don't have trigger_step

    def _trigger_epoch(self):
        tm = CallbackTimeLogger()

        test_sess_restored = False
        for cb in self.cbs:
            if isinstance(cb.type, TrainCallbackType):
                with tm.timed_callback(type(cb).__name__):
                    cb.trigger_epoch()
            else:
                if not test_sess_restored:
                    with tm.timed_callback('restore checkpoint'):
                        self.test_callback_context.restore_checkpoint()
                    test_sess_restored = True
                with self.test_callback_context.trigger_epoch_context(), \
                        tm.timed_callback(type(cb).__name__):
                    cb.trigger_epoch()
        tm.log()
