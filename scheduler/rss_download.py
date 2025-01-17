import os
import re
import requests
import log
from xml.dom.minidom import parse
import xml.dom.minidom

from config import get_config, RMT_MOVIETYPE, RMT_MEDIAEXT
from functions import is_chinese
from message.send import Message
from rmt.media import Media
from pt.qbittorrent import Qbittorrent
from pt.transmission import Transmission


class RSSDownloader:
    __rss_cache_list = []
    __running_flag = False

    __movie_subtypedir = True
    __tv_subtypedir = True
    __pt_client = None
    __movie_path = None
    __tv_path = None
    __rss_chinese = None
    __save_path = None

    message = None
    media = None
    qbittorrent = None
    transmission = None

    def __init__(self):
        self.message = Message()
        self.media = Media()

        config = get_config()
        if config.get('pt'):
            self.__rss_jobs = config['pt'].get('sites')
            self.__rss_chinese = config['pt'].get('rss_chinese')
            self.__pt_client = config['pt'].get('pt_client')
            if self.__pt_client == "qbittorrent":
                if config.get('qbittorrent'):
                    self.qbittorrent = Qbittorrent()
                    self.__save_path = config['qbittorrent'].get('save_path')
            elif self.__pt_client == "transmission":
                if config.get('transmission'):
                    self.transmission = Transmission()
                    self.__save_path = config['transmission'].get('save_path')
        if config.get('media'):
            self.__movie_path = config['media'].get('movie_path')
            self.__tv_path = config['media'].get('tv_path')
            self.__movie_subtypedir = config['media'].get('movie_subtypedir', True)
            self.__tv_subtypedir = config['media'].get('tv_subtypedir', True)

    def run_schedule(self):
        try:
            if self.__running_flag:
                log.error("【RUN】rssdownload任务正在执行中...")
            else:
                self.__rssdownload()
        except Exception as err:
            self.__running_flag = False
            log.error("【RUN】执行任务rssdownload出错：" + str(err))

    @staticmethod
    def __parse_rssxml(url):
        ret_array = []
        if not url:
            return ret_array
        try:
            log.info("【RSS】开始下载：" + url)
            ret = requests.get(url)
        except Exception as e:
            log.error("【RSS】下载失败：" + str(e))
            return ret_array
        if ret:
            ret_xml = ret.text
            try:
                # 解析XML
                dom_tree = xml.dom.minidom.parseString(ret_xml)
                rootNode = dom_tree.documentElement
                items = rootNode.getElementsByTagName("item")
                for item in items:
                    # 获取XML值
                    title = item.getElementsByTagName("title")[0].firstChild.data
                    category = item.getElementsByTagName("category")[0].firstChild.data
                    enclosure = item.getElementsByTagName("enclosure")[0].getAttribute("url")
                    tmp_dict = {'title': title, 'category': category, 'enclosure': enclosure}
                    ret_array.append(tmp_dict)
                log.info("【RSS】下载成功，发现更新：" + str(len(items)))
            except Exception as e2:
                log.error("【RSS】解析失败：" + str(e2))
                return ret_array
        return ret_array

    def __rssdownload(self):
        config = get_config()
        pt = config.get('pt')
        if not pt:
            return
        sites = pt.get('sites')
        if not sites:
            return
        log.info("【RSS】开始RSS订阅...")

        # 读取关键字配置
        movie_keys = pt.get('movie_keys')
        if not movie_keys:
            log.warn("【RSS】未配置电影订阅关键字！")
        else:
            if not isinstance(movie_keys, list):
                movie_keys = [movie_keys]
            log.info("【RSS】电影订阅规则清单：%s" % " ".join('%s' % key for key in movie_keys))

        tv_keys = pt.get('tv_keys')
        if not tv_keys:
            log.warn("【RSS】未配置电视剧订阅关键字！")
        else:
            if not isinstance(tv_keys, list):
                tv_keys = [tv_keys]
            log.info("【RSS】电视剧订阅规则清单：%s" % " ".join('%s' % key for key in tv_keys))

        if not movie_keys and not tv_keys:
            return

        self.__running_flag = True
        # 保存命中的资源信息
        __rss_download_torrents = []
        # 代码站点配置优先级的序号
        order_seq = 0
        for rss_job, job_info in sites.items():
            order_seq = order_seq + 1
            # 读取子配置
            rssurl = job_info.get('rssurl')
            if not rssurl:
                log.error("【RSS】%s 未配置rssurl，跳过..." % str(rss_job))
                continue
            res_type = job_info.get('res_type')
            if res_type and not isinstance(res_type, list):
                res_type = [res_type]

            # 开始下载RSS
            log.info("【RSS】正在处理：%s" % rss_job)
            rss_result = self.__parse_rssxml(rssurl)
            if len(rss_result) == 0:
                continue
            for res in rss_result:
                try:
                    title = res['title']
                    enclosure = res['enclosure']
                    # 判断是否处理过
                    if enclosure not in self.__rss_cache_list:
                        self.__rss_cache_list.append(enclosure)
                    else:
                        log.debug("【RSS】%s 已处理过，跳过..." % title)
                        continue

                    log.info("【RSS】开始检索媒体信息:" + title)

                    # 假定是电影
                    search_type = "电影"
                    # 判定是不是电视剧，如果是的话就是电视剧，否则就先按电影检索，电影检索不到时再按电视剧检索
                    if self.media.is_media_files_tv(title):
                        search_type = "电视剧"
                        media_info = self.media.get_media_info_on_name(title, search_type)
                    else:
                        # 按电影检索
                        media_info = self.media.get_media_info_on_name(title, search_type)
                        if not media_info or media_info['id'] == "0":
                            # 电影没有再按电视剧检索
                            search_type = "电视剧"
                            media_info = self.media.get_media_info_on_name(title, search_type)

                    if not media_info:
                        log.error("【RSS】%s 检索媒体信息出错！" % title)
                        continue

                    media_id = media_info["id"]
                    if media_id == "0":
                        continue

                    media_type = media_info["type"]
                    media_title = media_info["title"]
                    media_year = media_info["year"]
                    if media_info.get('vote_average'):
                        vote_average = media_info['vote_average']
                    else:
                        vote_average = ""
                    backdrop_path = self.media.get_backdrop_image(media_info.get('backdrop_path'), media_id)

                    if self.__rss_chinese and not is_chinese(media_title):
                        log.info("【RSS】该媒体在TMDB中没有中文描述，跳过：%s" % media_title)
                        continue

                    match_flag = False
                    # 按种子标题匹配
                    if search_type == "电影":
                        # 按电影匹配
                        for key in movie_keys:
                            if re.search(str(key), title):
                                match_flag = True
                                break
                        if match_flag:
                            log.info("【RSS】电影 %s 种子标题匹配成功!" % title)
                    else:
                        # 按电视剧匹配
                        for key in tv_keys:
                            if re.search(str(key), title):
                                match_flag = True
                                break
                        if match_flag:
                            log.info("【RSS】电视剧 %s 种子标题匹配成功!" % title)

                    # 按媒体信息匹配
                    if not match_flag:
                        if search_type == "电影":
                            # 按电影匹配
                            for key in movie_keys:
                                if str(key) == media_title:
                                    match_flag = True
                                    break
                            if match_flag:
                                log.info("【RSS】电影 %s 媒体名称匹配成功!" % media_title)
                        else:
                            # 按电视剧匹配
                            for key in tv_keys:
                                if str(key) == media_title:
                                    match_flag = True
                                    break
                            if match_flag:
                                log.info("【RSS】电视剧 %s 媒体名称匹配成功!" % media_title)

                    # 匹配后，看资源类型是否满足
                    # 代表资源类型在配置中的优先级顺序
                    res_order = 99
                    res_typestr = ""
                    if match_flag and res_type:
                        # 确定标题中是否有资源类型关键字，并返回关键字的顺序号
                        match_flag, res_order, res_typestr = self.media.check_resouce_types(title, res_type)
                        if not match_flag:
                            log.info("【RSS】%s 资源类型不匹配！" % title)

                    # 判断在媒体库中是否已存在...
                    if match_flag:
                        if media_year:
                            media_name = "%s (%s)" % (media_title, media_year)
                        else:
                            media_name = media_title
                        if search_type == "电影":
                            # 确认是否已存在
                            exist_flag = False
                            media_path = os.path.join(self.__movie_path, media_name)
                            if self.__movie_subtypedir:
                                for m_type in RMT_MOVIETYPE:
                                    media_path = os.path.join(self.__movie_path, m_type, media_name)
                                    # 目录是否存在
                                    if os.path.exists(media_path):
                                        exist_flag = True
                                        break
                            else:
                                exist_flag = os.path.exists(media_path)

                            if exist_flag:
                                log.info("【RSS】电影目录已存在该电影，跳过：%s" % media_path)
                                continue
                        else:
                            # 剧集目录
                            if self.__tv_subtypedir:
                                media_path = os.path.join(self.__tv_path, media_type, media_name)
                            else:
                                media_path = os.path.join(self.__tv_path, media_name)
                            # 剧集是否存在
                            # Sxx
                            file_season = self.media.get_media_file_season(title)
                            # 季 Season xx
                            season_str = "Season " + str(int(file_season.replace("S", "")))
                            season_dir = os.path.join(media_path, season_str)
                            # Exx
                            file_seq = self.media.get_media_file_seq(title)
                            if file_seq != "":
                                # 集 xx
                                file_seq_num = str(int(file_seq.replace("E", "").replace("P", "")))
                                # 文件路径
                                file_path = os.path.join(season_dir,
                                                         media_title + " - " +
                                                         file_season + file_seq + " - " +
                                                         "第 " + file_seq_num + " 集")
                                exist_flag = False
                                for ext in RMT_MEDIAEXT:
                                    log.debug("【RSS】路径：" + file_path + ext)
                                    if os.path.exists(file_path + ext):
                                        exist_flag = True
                                        log.warn("【RSS】该剧集文件已存在，跳过：%s" % (file_path + ext))
                                        break
                                if exist_flag:
                                    continue
                        # site_order res_order 从小到大排序
                        res_info = {"site_order": order_seq,
                                    "site": rss_job,
                                    "type": search_type,
                                    "title": media_title,
                                    "year": media_year,
                                    "enclosure": enclosure,
                                    "torrent_name": title,
                                    "vote_average": vote_average,
                                    "res_order": res_order,
                                    "res_type": res_typestr,
                                    "backdrop_path": backdrop_path}
                        if res_info not in __rss_download_torrents:
                            __rss_download_torrents.append(res_info)
                    else:
                        log.info("【RSS】当前资源与规则不匹配，跳过...")
                except Exception as e:
                    log.error("【RSS】错误：%s" % str(e))
                    continue
            log.info("【RSS】%s 处理结束！" % rss_job)

        # 所有site都检索完成，开始选种下载
        can_download_list = []
        can_download_list_item = []
        if __rss_download_torrents:
            # 按真实名称、站点序号、资源序号进行排序
            __rss_download_torrents = sorted(__rss_download_torrents,
                                             key=lambda x: x['title'] + str(x['site_order']) + str(x['res_order']))
            # 排序后重新加入数组，按真实名称控重，即只取每个名称的第一个
            for t_item in __rss_download_torrents:
                media_name = "%s (%s)" % (t_item.get('title'), t_item.get('year'))
                if media_name not in can_download_list:
                    can_download_list.append(media_name)
                    can_download_list_item.append(t_item)

        # 开始添加下载
        for can_item in can_download_list_item:
            try:
                # 添加PT任务
                log.info("【RSS】添加PT任务：%s，url= %s" % (can_item.get('title'), can_item.get('enclosure')))
                ret = None
                if self.__pt_client == "qbittorrent":
                    if self.qbittorrent:
                        ret = self.qbittorrent.add_qbittorrent_torrent(can_item.get('enclosure'), self.__save_path)
                elif self.__pt_client == "transmission":
                    if self.transmission:
                        ret = self.transmission.add_transmission_torrent(can_item.get('enclosure'),
                                                                         self.__save_path)
                else:
                    log.error("【RSS】PT下载软件配置有误！")
                    return
                if ret:
                    tt = can_item.get('title')
                    va = can_item.get('vote_average')
                    yr = can_item.get('year')
                    bp = can_item.get('backdrop_path')
                    tp = can_item.get('type')
                    se = self.media.get_sestring_from_name(can_item.get('torrent_name'))
                    msg_title = tt
                    if yr:
                        msg_title = msg_title + " (%s)" % str(yr)
                    if se:
                        msg_text = "来自RSS的%s %s %s 已开始下载" % (tp, msg_title, se)
                    else:
                        msg_text = "来自RSS的%s %s 已开始下载" % (tp, msg_title)
                    if va and va != '0':
                        msg_title = msg_title + " 评分：%s" % str(va)

                    self.message.sendmsg(msg_title, msg_text, bp)

            except Exception as e:
                log.error("【RSS】添加PT任务出错：%s" % str(e))
        self.__running_flag = False
        log.info("【RSS】RSS订阅处理完成！")


if __name__ == "__main__":
    RSSDownloader().run_schedule()
