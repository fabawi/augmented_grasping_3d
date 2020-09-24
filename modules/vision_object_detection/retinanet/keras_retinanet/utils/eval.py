"""
Copyright 2017-2018 Fizyr (https://fizyr.com)

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from .anchors import compute_overlap
from .visualization import draw_detections, draw_annotations

import keras
import numpy as np
import os

import cv2
import progressbar
assert(callable(progressbar.progressbar)), "Using wrong progressbar module, install 'progressbar2' instead."


def _compute_ap(recall, precision):
    """ Compute the average precision, given the recall and precision curves.

    Code originally from https://github.com/rbgirshick/py-faster-rcnn.

    # Arguments
        recall:    The recall curve (list).
        precision: The precision curve (list).
    # Returns
        The average precision as computed in py-faster-rcnn.
    """
    # correct AP calculation
    # first append sentinel values at the end
    mrec = np.concatenate(([0.], recall, [1.]))
    mpre = np.concatenate(([0.], precision, [0.]))

    # compute the precision envelope
    for i in range(mpre.size - 1, 0, -1):
        mpre[i - 1] = np.maximum(mpre[i - 1], mpre[i])

    # to calculate area under PR curve, look for points
    # where X axis (recall) changes value
    i = np.where(mrec[1:] != mrec[:-1])[0]

    # and sum (\Delta recall) * prec
    ap = np.sum((mrec[i + 1] - mrec[i]) * mpre[i + 1])
    return ap


def _get_detections(generator, model, score_threshold=0.05, max_detections=1024, save_path=None):
    """ Get the detections from the model using the generator.

    The result is a list of lists such that the size is:
        all_detections[num_images][num_classes] = detections[num_detections, 4 + num_classes]

    # Arguments
        generator       : The generator used to run images through the model.
        model           : The model to run on the images.
        score_threshold : The score confidence threshold to use.
        max_detections  : The maximum number of detections to use per image.
        save_path       : The path to save the images with visualized detections to.
    # Returns
        A list of lists containing the detections for each image in the generator.
    """
    all_detections = [[None for i in range(generator.num_classes()) if generator.has_label(i)] for j in range(generator.size())]

    for i in progressbar.progressbar(range(generator.size()), prefix='Running RetinaNet evaluation network: '):
        raw_image    = generator.load_image(i)
        image        = generator.preprocess_image(raw_image.copy())
        image, scale = generator.resize_image(image)

        if keras.backend.image_data_format() == 'channels_first':
            image = image.transpose((2, 0, 1))

        # run network
        boxes, scores, labels = model.predict_on_batch(np.expand_dims(image, axis=0))[:3]

        # correct boxes for image scale
        boxes /= scale

        # select indices which have a score above the threshold
        indices = np.where(scores[0, :] > score_threshold)[0]

        # select those scores
        scores = scores[0][indices]

        # find the order with which to sort the scores
        scores_sort = np.argsort(-scores)[:max_detections]

        # select detections
        image_boxes      = boxes[0, indices[scores_sort], :]
        image_scores     = scores[scores_sort]
        image_labels     = labels[0, indices[scores_sort]]
        image_detections = np.concatenate([image_boxes, np.expand_dims(image_scores, axis=1), np.expand_dims(image_labels, axis=1)], axis=1)

        if save_path is not None:
            # TODO (fabawi): restore the annotations drawing. commented for now for debugging
            # draw_annotations(raw_image, generator.load_annotations(i), label_to_name=generator.label_to_name)
            draw_detections(raw_image, image_boxes, image_scores, image_labels, label_to_name=generator.label_to_name)

            cv2.imwrite(os.path.join(save_path, '{}.png'.format(i)), raw_image)

        # copy detections to all_detections
        for label in range(generator.num_classes()):
            if not generator.has_label(label):
                continue

            all_detections[i][label] = image_detections[image_detections[:, -1] == label, :-1]

    return all_detections


def _get_annotations(generator):
    """ Get the ground truth annotations from the generator.

    The result is a list of lists such that the size is:
        all_detections[num_images][num_classes] = annotations[num_detections, 5]

    # Arguments
        generator : The generator used to retrieve ground truth annotations.
    # Returns
        A list of lists containing the annotations for each image in the generator.
    """
    all_annotations = [[None for i in range(generator.num_classes())] for j in range(generator.size())]

    for i in progressbar.progressbar(range(generator.size()), prefix='Parsing annotations: '):
        # load the annotations
        annotations = generator.load_annotations(i)

        # copy detections to all_annotations
        for label in range(generator.num_classes()):
            if not generator.has_label(label):
                continue

            if 'grid_locations' in annotations:
                bboxes = annotations['bboxes'][annotations['labels'] == label, :].copy()
                grid_locations = annotations['grid_locations'][annotations['labels'] == label, :].copy()
                all_annotations[i][label] = np.concatenate((bboxes, grid_locations), axis=1)
            else:
                all_annotations[i][label] = annotations['bboxes'][annotations['labels'] == label, :].copy()

    return all_annotations


def evaluate(
    generator,
    model,
    iou_threshold=0.5,
    score_threshold=0.05,
    max_detections=100,
    max_detections_per_bounding_box=1,
    location_bias=False,
    save_path=None
):
    """ Evaluate a given dataset using a given model.

    # Arguments
        generator       : The generator that represents the dataset to evaluate.
        model           : The model to evaluate.
        iou_threshold   : The threshold used to consider when a detection is positive or negative.
        score_threshold : The score confidence threshold to use for detections.
        max_detections  : The maximum number of detections to use per image.
        max_detections_per_bounding_box   : The maximum number of detections per bounding box
        location_bias   : Evaluate the results of the mAP based on grid locations of the blocks
        save_path       : The path to save images with visualized detections to.
    # Returns
        A dict mapping class names to mAP scores.
    """
    # gather all detections and annotations
    all_detections     = _get_detections(generator, model, score_threshold=score_threshold, max_detections=max_detections, save_path=save_path)
    all_annotations    = _get_annotations(generator)
    average_precisions = {}
    location_precisions = {}
    # all_detections = pickle.load(open('all_detections.pkl', 'rb'))
    # all_annotations = pickle.load(open('all_annotations.pkl', 'rb'))
    # pickle.dump(all_detections, open('all_detections.pkl', 'wb'))
    # pickle.dump(all_annotations, open('all_annotations.pkl', 'wb'))

    # location bias variables
    if location_bias:
        loc_false_positives = {}
        loc_true_positives = {}
        loc_scores = {}
        loc_num_annotations = {}

    # process detections and annotations
    for label in range(generator.num_classes()):
        if not generator.has_label(label):
            continue

        false_positives = np.zeros((0,))
        true_positives  = np.zeros((0,))
        scores          = np.zeros((0,))
        num_annotations = 0.0

        for i in range(generator.size()):
            detections           = all_detections[i][label]
            annotations          = all_annotations[i][label]
            num_annotations     += annotations.shape[0]
            detected_annotations = []

            if location_bias:
                loc_labels = []
                for annotation in annotations:
                    # remember that we ignore the z axis and compute the mAP for each (x,y) coincidence
                    loc_labels.append('_'.join("%g" % np.round(g, 2) for g in annotation[4:-1]))
                    loc_label = loc_labels[-1]
                    if loc_label not in loc_num_annotations:
                        loc_num_annotations[loc_labels[-1]] = 0.0
                    else:
                        loc_num_annotations[loc_labels[-1]] += 1

                    if loc_label not in loc_scores:
                        loc_scores[loc_label] = np.zeros((0,))
                    if loc_label not in loc_false_positives:
                        loc_false_positives[loc_label] = np.zeros((0,))
                    if loc_label not in loc_true_positives:
                        loc_true_positives[loc_label] = np.zeros((0,))

            for d in detections:
                scores = np.append(scores, d[4])

                if annotations.shape[0] == 0:
                    false_positives = np.append(false_positives, 1)
                    true_positives  = np.append(true_positives, 0)
                    continue

                # compute overlap on bboxes only
                overlaps = compute_overlap(np.expand_dims(d, axis=0), annotations[:, :4])
                assigned_annotations = np.argsort(-overlaps, axis=1)
                assigned_annotation = assigned_annotations[:, 0]  # np.argmax(overlaps, axis=1)

                if assigned_annotation in detected_annotations:
                    for z in range(1, max_detections_per_bounding_box):
                        if z >= assigned_annotations.shape[1]:
                            break
                        assigned_annotation = assigned_annotations[:, z]
                        if assigned_annotation not in detected_annotations:
                            break

                if location_bias:
                    loc_label = loc_labels[int(assigned_annotation)]
                    loc_scores[loc_label] = np.append(loc_scores[loc_label], d[4])

                max_overlap = overlaps[0, assigned_annotation]
                if max_overlap >= iou_threshold and assigned_annotation not in detected_annotations:
                    false_positives = np.append(false_positives, 0)
                    true_positives  = np.append(true_positives, 1)
                    detected_annotations.append(assigned_annotation)
                    if location_bias:
                        loc_false_positives[loc_label] = np.append(loc_false_positives[loc_label], 0)
                        loc_true_positives[loc_label] = np.append(loc_true_positives[loc_label], 1)

                else:
                    false_positives = np.append(false_positives, 1)
                    true_positives  = np.append(true_positives, 0)
                    if location_bias:
                        loc_false_positives[loc_label] = np.append(loc_false_positives[loc_label], 1)
                        loc_true_positives[loc_label] = np.append(loc_true_positives[loc_label], 0)

        # no annotations -> AP for this class is 0 (is this correct?)
        if num_annotations == 0:
            average_precisions[label] = 0, 0
            continue

        # sort by score
        indices         = np.argsort(-scores)
        false_positives = false_positives[indices]
        true_positives  = true_positives[indices]

        # compute false positives and true positives
        false_positives = np.cumsum(false_positives)
        true_positives  = np.cumsum(true_positives)

        # compute recall and precision
        recall    = true_positives / num_annotations
        precision = true_positives / np.maximum(true_positives + false_positives, np.finfo(np.float64).eps)

        # compute average precision
        average_precision  = _compute_ap(recall, precision)
        average_precisions[label] = average_precision, num_annotations

    # compute the mAP for the grid locations
    if location_bias:
        for label in loc_num_annotations.keys():
            # no annotations -> AP for this class is 0 (is this correct?)
            if loc_num_annotations[label] == 0:
                location_precisions[label] = 0, 0
                continue

            # sort by score
            indices = np.argsort(-loc_scores[label])
            loc_false_positives[label] = loc_false_positives[label][indices]
            loc_true_positives[label] = loc_true_positives[label][indices]

            # compute false positives and true positives
            loc_false_positives[label]  = np.cumsum(loc_false_positives[label])
            loc_true_positives[label] = np.cumsum(loc_true_positives[label])

            # compute recall and precision
            loc_recall = loc_true_positives[label] / loc_num_annotations[label]
            loc_precision = loc_true_positives[label] / np.maximum(loc_true_positives[label] + loc_false_positives[label], np.finfo(np.float64).eps)

            # compute average precision
            location_precision = _compute_ap(loc_recall, loc_precision)
            location_precisions[label] = location_precision, loc_num_annotations[label]

    return average_precisions, location_precisions
